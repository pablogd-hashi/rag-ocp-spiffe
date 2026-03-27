# Vault Performance Replication — Operational Runbook

## Overview

Performance replication (PR) enables horizontal scaling of Vault Enterprise
by replicating secrets, auth methods, policies, and encryption keys from a
primary cluster to one or more secondary clusters in real time. Organizations
have successfully deployed tens of PR replicas in production.

Reference: HashiCorp Validated Design — Vault Operating Guide for Scaling.

---

## Architecture

### Topology

Vault uses a one-to-many leader/follower model. A single primary cluster
streams its write-ahead log (WAL) to multiple secondary clusters. There is
no hard technical limit on the number of secondaries, but practical limits
depend on write intensity.

### Communication Channels

| Port | Purpose |
|------|---------|
| TCP 8200 | Vault API — initial authentication during replication setup |
| TCP 8201 | Cluster port — replication traffic via HTTP/2 gRPC, mutual TLS |

All communication between primaries and secondaries is end-to-end encrypted
with mutually-authenticated TLS sessions established via replication tokens
exchanged during configuration.

### Dual Role Capability

A cluster can simultaneously serve as:

- DR primary + PR primary
- DR primary + PR secondary

A cluster **cannot** be both DR secondary and PR secondary at the same time.

---

## What Replicates (and What Does Not)

### Replicated

- Secrets, auth methods, authorization policies, encryption keys, and
  configuration data.

### Not Replicated

- **Tokens and leases** — secondaries generate their own.
- **Integrated storage snapshots** — must be configured independently on
  each cluster.
- **Audit device destinations** — the configuration replicates, but the
  target directory or endpoint must exist on every cluster node.

### Selective Replication

Use **path filters** and **local mounts** to control which secrets engines
replicate to which secondaries. This is useful for data sovereignty
requirements.

---

## Token Portability

| Token type | Portable across PR clusters? |
|------------|------------------------------|
| Service tokens | No — clients must re-authenticate per cluster |
| Orphan batch tokens | Yes — contain embedded auth information valid across all clusters |

For cross-cluster workflows (CI/CD pipelines that hit multiple regions),
use orphan batch tokens to avoid per-cluster authentication overhead.

---

## Request Routing

Secondary clusters handle these operations locally without consulting the
primary:

- Authentication and token generation
- KV reads
- Transit encrypt / decrypt
- PKI certificate issuance (when `no_store=true`)
- Dynamic secret lease generation

Shared-state modifications (policy changes, secret writes, auth method
configuration) are transparently forwarded to the primary.

---

## Disaster Recovery Failover

When the primary fails:

1. Promote the DR secondary to become the new primary.
2. PR secondaries store the `failover_addr` of the original primary's DR
   secondaries — they will attempt automatic reconnection to the promoted
   DR primary.
3. If automatic reconnection fails (network path changed), manually update
   the primary address on each PR secondary:

```
POST /sys/replication/performance/secondary/update-primary
```

**Requirement:** PR secondary nodes must be able to reach the promoted DR
primary over TCP 8201 for automatic failover to work.

---

## Networking

### Load Balancer Rules

- Route TCP 8201 traffic directly to the primary cluster leader. Use health
  checks to determine leader status.
- **Do not terminate TLS** on the load balancer for TCP 8201 — mutual TLS
  between cluster leaders requires direct, unintercepted connections.
- Override the primary cluster address when the cluster is only accessible
  through a load-balanced VIP.

### Replication Establishment Flow

1. Secondary leader sends an authentication request over TCP 8200 using an
   encoded replication token.
2. Secondary obtains the cluster address list from the primary.
3. Secondary initiates WAL streaming on TCP 8201 with the primary leader.

---

## Auto-Unseal

Each cluster maintains an independent KMS or HSM unseal mechanism. This
enables regional key management — the US cluster can use AWS KMS in
`us-east-1` while the EU cluster uses AWS KMS in `eu-west-1`.

Secondary clusters inherit recovery keys and recovery key configuration from
the primary.

---

## Monitoring

### Health Check Endpoint

```
GET /sys/replication/performance/status
```

The `state` field should never be `idle` on an active secondary. If it is,
the replication link is down.

### Key Metrics

| Metric | What it measures |
|--------|------------------|
| `vault.wal_persistwals` | Time to persist WAL entries to storage |
| `vault.wal_flushready` | Time to flush ready WAL entries |
| `last_performance_wal` (primary) | Latest WAL index on the primary |
| `last_remote_wal` (secondary) | Latest WAL index received by the secondary |

**Replication lag:** compare `last_performance_wal` on the primary with
`last_remote_wal` on each secondary. The values should converge shortly after
writes. Growing divergence indicates storage backend pressure or network
issues.

Watch for increasing `wal_persistwals` and `wal_flushready` — these signal
WAL accumulation and potential storage throughput problems.

---

## Scaling Use Cases

1. **Horizontal read scaling** — add PR secondaries to absorb Transit,
   Transform, and KV read load in each region.
2. **Latency reduction** — deploy secondaries close to application clusters
   so authentication and encryption operations stay local.
3. **Adding non-voter nodes** — within a single cluster, non-voter
   (performance standby) nodes increase capacity for read-oriented operations
   like Transit and PKI issuance without participating in Raft consensus.
