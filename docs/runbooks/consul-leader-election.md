# Consul Leader Election Failure — Runbook

## Overview

This runbook covers the diagnosis, recovery, and post-recovery validation
steps for a Consul cluster that has lost quorum or is experiencing leader
election failures. Leader instability typically manifests as:

- Flapping health checks across all services.
- `No cluster leader` errors in service mesh proxies.
- Raft log warnings in `consul monitor -log-level=warn`.

## Prerequisites

- `consul` CLI configured with a management token.
- SSH access to at least one server node.
- Familiarity with the Raft consensus protocol.

---

## Diagnosis

### Step 1 — Check cluster members

```bash
consul members -detailed
```

All server nodes should report `alive` with a consistent `vsn` field.
If a node shows `left` or `failed`, it has departed the cluster.

### Step 2 — Inspect Raft peers

```bash
consul operator raft list-peers
```

A healthy 3-node cluster should show exactly three `voter` entries.
If a peer shows `(unknown)` as leader, quorum has been lost.

### Step 3 — Review server logs

```bash
journalctl -u consul -n 200 --no-pager | grep -i "election\|leader\|heartbeat"
```

Look for:

- `heartbeat timeout reached, starting election` — the follower is not
  hearing from the leader.
- `failed to make requestVote RPC` — network partition between servers.
- `replication to peer … is not progressing` — disk I/O bottleneck on
  the leader.

---

## Recovery

### Case A — One server lost, quorum intact

If two of three servers are healthy:

1. Remove the dead peer:
   ```bash
   consul operator raft remove-peer -address=<dead-node>:8300
   ```
2. Provision a replacement server with the same datacenter name.
3. Join the replacement to the cluster:
   ```bash
   consul join <existing-server-ip>
   ```
4. Verify three voters with `consul operator raft list-peers`.

### Case B — Quorum lost (majority of servers down)

This requires the **outage recovery** procedure:

1. Stop Consul on ALL remaining servers.
2. On the single surviving server, create `raft/peers.json`:
   ```json
   [
     {
       "id": "<node-id>",
       "address": "<node-ip>:8300",
       "non_voter": false
     }
   ]
   ```
3. Start Consul on that server. It will bootstrap as a single-node cluster.
4. Join fresh servers one at a time until you reach three voters.
5. Remove the `peers.json` file — it is only read once at startup.

### Case C — Leader flapping (all nodes alive)

Often caused by resource contention:

1. Check CPU and disk latency on the leader node.
2. Increase `performance.raft_multiplier` temporarily:
   ```hcl
   performance {
     raft_multiplier = 3
   }
   ```
3. Reload Consul: `consul reload`.
4. Investigate the underlying resource issue (noisy neighbour, full disk,
   network saturation).

---

## Post-Recovery Validation

1. **Leader stable for 5 minutes:**
   ```bash
   consul operator raft list-peers  # one node shows "leader"
   ```
2. **All services healthy:**
   ```bash
   consul catalog services | xargs -I{} consul health checks {}
   ```
3. **KV store read/write test:**
   ```bash
   consul kv put test/canary "$(date -u +%s)"
   consul kv get test/canary
   consul kv delete test/canary
   ```
4. **Service mesh connectivity:** run a curl from a Connect-enabled
   sidecar to verify mTLS upstream routing:
   ```bash
   curl -s http://localhost:19000/clusters | grep -c "healthy"
   ```

If all four checks pass, the cluster is recovered. Update the incident
timeline and schedule a blameless post-mortem within 48 hours.
