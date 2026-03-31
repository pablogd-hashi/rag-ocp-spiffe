# OpenShift Deployment Architecture

This document describes the architecture of the RAG platform on OpenShift Local (CRC),
with Vault PKI as the Consul Connect Certificate Authority and full mTLS between all
services via SPIFFE identities.

## Overview

The platform runs across three namespaces:

| Namespace | Contents |
|---|---|
| `vault` | Vault dev server — PKI backend for the mesh |
| `consul` | Consul server — service mesh control plane |
| `rag-platform` | RAG services — all connected via Consul Connect mTLS |

Services communicate with each other through Envoy sidecar proxies. Envoy handles
TLS termination, certificate verification, and Service Intention enforcement so the
application code connects to `localhost` and has no awareness of encryption.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          OpenShift Cluster (CRC)                              │
│                                                                               │
│  ┌─────────────────────────────────────┐                                     │
│  │  Namespace: vault                    │                                     │
│  │                                      │                                     │
│  │  Vault (dev mode)                    │                                     │
│  │  ├─ PKI: connect_root  (root CA)     │                                     │
│  │  └─ PKI: connect_inter (inter CA) ──┼──► signs SPIFFE SVIDs               │
│  │     Accessible at localhost:8200     │                                     │
│  └─────────────────────────────────────┘                                     │
│                     │ Kubernetes auth (consul ServiceAccounts)               │
│                     ▼                                                         │
│  ┌─────────────────────────────────────┐                                     │
│  │  Namespace: consul                   │                                     │
│  │                                      │                                     │
│  │  Consul server                       │                                     │
│  │  ├─ Connect CA provider: Vault PKI   │                                     │
│  │  ├─ Service registry                 │                                     │
│  │  ├─ Service Intentions (allow/deny)  │                                     │
│  │  └─ Sidecar injector webhook      ──┼──► injects Envoy into each pod      │
│  │     Accessible at localhost:8500     │                                     │
│  └─────────────────────────────────────┘                                     │
│                     │ issues SPIFFE SVIDs via Envoy bootstrap                │
│                     ▼                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  Namespace: rag-platform                                                  │ │
│  │                                                                           │ │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐        │ │
│  │  │  UI        │  │  Query     │  │  Qdrant    │  │  Ollama    │        │ │
│  │  │  :8501     │  │  Service   │  │  :6333     │  │  :11434    │        │ │
│  │  │  [app]     │  │  :8000     │  │  [app]     │  │  [app]     │        │ │
│  │  │  [envoy]──►│  │  [app]     │  │  [envoy]   │  │  [envoy]   │        │ │
│  │  └────────────┘  │  [envoy]──►│  └────────────┘  └────────────┘        │ │
│  │                  └────────────┘                                           │ │
│  │  All arrows = mTLS via SPIFFE certificates from Vault PKI                │ │
│  │  Service Intentions enforce which arrows are allowed                     │ │
│  │                                                                           │ │
│  │  Ingest Job (batch, runs once)                                           │ │
│  │  - reads docs from ConfigMap                                             │ │
│  │  - embeds with nomic-embed-text (via Envoy → ollama)                     │ │
│  │  - writes vectors to Qdrant (via Envoy → qdrant)                         │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                     │                                                         │
│                     │ OpenShift Route (HTTPS/TLS termination)                │
│                     ▼                                                         │
│              User browser                                                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Vault (PKI backend)

**Namespace:** `vault`
**Mode:** Dev (in-memory, root token: `root`)
**Role:** Certificate Authority for the Consul service mesh

Vault runs two PKI mounts:

| Mount | Purpose | TTL |
|---|---|---|
| `connect_root` | Root CA — signs the intermediate only | 10 years |
| `connect_inter` | Intermediate CA — signs SPIFFE leaf certs | 1 year |

Consul authenticates to Vault via the Kubernetes auth method using its
ServiceAccount token. It holds a policy (`consul-connect-ca`) that allows
it to sign certificates via `connect_inter`.

**UIs:**
- Vault UI: `http://localhost:8200` (token: `root`)
- Navigate to Secrets → `connect_inter` → Certificates to see every SVID issued

### 2. Consul (service mesh control plane)

**Namespace:** `consul`
**Deployed via:** Helm (`hashicorp/consul`)
**Role:** mTLS enforcement, service registry, Service Intentions

Consul is configured with `connectCA.provider: vault`, delegating all certificate
signing to Vault PKI. When a pod annotated with
`consul.hashicorp.com/connect-inject: "true"` starts:

1. Consul's admission webhook injects an Envoy sidecar
2. An init container sets iptables rules to redirect all traffic through Envoy
3. Envoy requests a SPIFFE SVID from Consul
4. Consul requests a signing from Vault PKI (`connect_inter`)
5. The signed certificate is delivered to Envoy
6. Envoy uses the certificate for all inbound and outbound mTLS connections

**UIs:**
- Consul UI: `http://localhost:8500` (port-forward started automatically by `task setup:ocp`)
- Services tab shows topology and mTLS status
- Intentions tab shows the allow/deny rules

### 3. RAG services (rag-platform namespace)

All services run with Consul Connect sidecars. They connect to upstream services
via `localhost` — the Envoy proxy handles routing and mTLS transparently.

| Service | Type | Envoy upstreams |
|---|---|---|
| ui | Deployment | query-service:8000 |
| query-service | Deployment | qdrant:6333, ollama:11434 |
| qdrant | StatefulSet | — (receives connections only) |
| ollama | Deployment | — (receives connections only) |
| ingest | Job | qdrant:6333, ollama:11434 |

**Credentials:** None. Services do not hold API keys or passwords. Authentication
is the mTLS handshake — Envoy verifies the peer's SPIFFE certificate before
forwarding a single byte.

**Service Intentions** (`k8s/consul/intentions.yaml`) define the explicit
allow-list. Any connection not listed is denied at the Envoy layer.

## mTLS certificate flow

```
1. Pod created with consul.hashicorp.com/connect-inject: "true"
2. Consul webhook injects Envoy sidecar + init container
3. Envoy requests certificate: Consul Connect CA API
4. Consul delegates CSR to Vault: POST connect_inter/sign/leaf
5. Vault signs certificate with SPIFFE SAN:
       spiffe://dc1/ns/rag-platform/svc/<service-name>
6. Envoy receives certificate (72h TTL)
7. Envoy rotates automatically before expiry
8. On every connection: both sides verify each other's SPIFFE cert
9. Envoy checks Service Intention: is this caller allowed?
10. If yes: traffic forwarded to app on localhost
    If no: TCP reset, nothing reaches the app
```

## Networking

Services connect via Consul Connect upstreams (localhost aliases):

```
App code                Envoy proxy           Destination
──────────────────────────────────────────────────────────
localhost:8000   ──►    mTLS to              query-service pod
localhost:6333   ──►    mTLS to              qdrant pod
localhost:11434  ──►    mTLS to              ollama pod
```

External access:

| Access | Method |
|---|---|
| Streamlit UI | OpenShift Route → `https://ui-rag-platform.apps-crc.testing` |
| Vault UI | `oc port-forward` → `http://localhost:8200` |
| Consul UI | `oc port-forward` → `http://localhost:8500` |

## Storage

| Volume | Size | Used by |
|---|---|---|
| Qdrant storage | 10 Gi PVC | Qdrant StatefulSet |
| Ollama models | 20 Gi PVC | Ollama Deployment |
| Platform docs | ConfigMap | Ingest Job |

## Security model

| Layer | Mechanism |
|---|---|
| Service authentication | SPIFFE SVIDs (mTLS) — no passwords or API keys |
| Service authorization | Consul Service Intentions (deny-by-default) |
| Certificate issuance | Vault PKI (`connect_inter`) |
| Certificate rotation | Automatic (Envoy renews before 72h TTL expires) |
| Pod security | `runAsNonRoot`, `hostUsers: false`, `seccompProfile: RuntimeDefault` |
| Image provenance | Built locally, pushed to CRC internal registry |

## Deployment commands

```bash
task demo:ocp      # full demo: setup + deploy + walkthrough
task setup:ocp     # infrastructure only: CRC + Vault + Consul
task status:ocp    # check all pods and routes
task clean:ocp     # tear everything down
```

## References

- Consul Connect CA: https://developer.hashicorp.com/consul/docs/connect/ca/vault
- SPIFFE spec: https://spiffe.io/docs/latest/spiffe-about/overview/
- Vault PKI: https://developer.hashicorp.com/vault/docs/secrets/pki
