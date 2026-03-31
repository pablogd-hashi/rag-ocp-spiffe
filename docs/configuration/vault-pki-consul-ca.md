# Vault PKI as Consul Connect Certificate Authority

This guide explains how Vault's PKI engine acts as the Certificate Authority (CA)
for Consul Connect's service mesh. Instead of using Consul's built-in CA,
all SPIFFE certificates are signed by Vault, giving you a single, auditable
root of trust visible in the Vault UI.

## Why Vault PKI instead of Consul's built-in CA

Consul ships with an internal CA, but using it means certificates are rooted in
Consul's own gossip key — opaque and not inspectable. With Vault as the CA:

- **Auditable**: every issued certificate is visible in the Vault UI under
  `connect_root` and `connect_inter`
- **Centralized**: one trust root for both mTLS and any future TLS workloads
- **Rotatable**: the intermediate CA (`connect_inter`) can be rotated by Consul
  without re-issuing the root
- **Policy-controlled**: Vault policies govern who can sign certificates

## Two-tier PKI hierarchy

```
Vault PKI mount: connect_root  (10-year root CA)
  │  — Never issues leaf certificates directly
  │  — Visible at Vault UI → Secrets → connect_root
  │
  └─► Vault PKI mount: connect_inter  (1-year intermediate CA)
        │  — Consul rotates this periodically
        │  — Visible at Vault UI → Secrets → connect_inter
        │
        └─► SPIFFE SVID (short-lived leaf cert per service)
              — Presented by each Envoy sidecar for mTLS
              — SAN: spiffe://dc1/ns/rag-platform/svc/<name>
```

## How Consul authenticates to Vault

Consul server pods use the Kubernetes auth method — they present their
ServiceAccount JWT to Vault, receive a short-lived token, and use it to sign
certificates via the `connect_inter` PKI mount.

```
consul-server pod  →  Vault /auth/kubernetes/login  →  token with consul-connect-ca policy
                                                              │
                                                              └─► connect_inter/sign/*
```

No long-lived credentials are stored anywhere. Vault issues tokens with a 1-hour
TTL; Consul renews automatically.

## Vault policy

The `consul-connect-ca` policy allows Consul full control of both PKI mounts:

```hcl
path "connect_root/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "connect_inter/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
```

## Consul Helm configuration

The key section in `k8s/consul/consul-values.yaml` that wires Consul to Vault PKI:

```yaml
global:
  connectCA:
    provider: "vault"
    vaultConfig:
      address: "http://vault.vault.svc.cluster.local:8200"
      token: "root"
      rootPKIPath: "connect_root"
      intermediatePKIPath: "connect_inter"
```

With this config, Consul will:
1. Connect to Vault on startup
2. Check whether `connect_inter` already has an intermediate CA
3. If not, generate a CSR and have `connect_root` sign it
4. Use `connect_inter` to issue leaf SVIDs for every service in the mesh

## Inspecting certificates in the Vault UI

Open `http://localhost:8200` (token: `root`) and navigate to:

- **Secrets → connect_root → Certificates** — the root CA cert (1 entry)
- **Secrets → connect_inter → Certificates** — the intermediate cert + all issued SVIDs

Each SVID entry shows:
- Serial number
- Subject: `spiffe://dc1/ns/rag-platform/svc/<service-name>`
- Validity period (72 hours by default)
- Issuing CA

## Inspecting the mesh in the Consul UI

Open `http://localhost:8500` and navigate to:

- **Services** — all registered services with their health status
- **Intentions** — which services are allowed to communicate
- **Nodes** — the Envoy sidecar certificates per node (click a service → Instances)

## SPIFFE identity format

Every service in `rag-platform` gets a SPIFFE identity of the form:

```
spiffe://dc1/ns/rag-platform/svc/<service-name>
```

| Service | SPIFFE ID |
|---|---|
| query-service | `spiffe://dc1/ns/rag-platform/svc/query-service` |
| qdrant | `spiffe://dc1/ns/rag-platform/svc/qdrant` |
| ollama | `spiffe://dc1/ns/rag-platform/svc/ollama` |
| ui | `spiffe://dc1/ns/rag-platform/svc/ui` |
| ingest | `spiffe://dc1/ns/rag-platform/svc/ingest` |

These identities are used in Service Intentions to authorize communication.
A service presenting the wrong certificate is denied before any application
traffic is exchanged.
