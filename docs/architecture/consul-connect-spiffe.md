# Consul Connect with SPIFFE on OpenShift

This document describes how Consul Connect provides mTLS service mesh capabilities with SPIFFE identities for the RAG platform on OpenShift.

## Overview

Consul Connect automatically secures service-to-service communication using mutual TLS (mTLS). Each service receives a unique SPIFFE identity and communicates through Envoy sidecar proxies that handle certificate management, encryption, and authorization.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        OpenShift Cluster (CRC)                           │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │               Vault PKI (namespace: vault)                      │    │
│  │  - connect_root  (root CA, 10-year TTL)                         │    │
│  │  - connect_inter (intermediate CA, rotated by Consul)           │    │
│  │  - Issues SPIFFE SVIDs via Consul Connect CA provider           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                  │                                       │
│                                  │ CA delegation                         │
│                                  ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    Consul Server (namespace: consul)                │ │
│  │  - Service Discovery                                                │ │
│  │  - Connect CA (backed by Vault PKI — not built-in CA)              │ │
│  │  - Service Intentions (Authorization Policies)                      │ │
│  │  - SPIFFE Identity Management                                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                  │                                       │
│                                  │ Issues certificates (SPIFFE SVIDs)    │
│                                  ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │              RAG Platform Services (namespace: rag-platform)        │ │
│  │                                                                      │ │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐ │ │
│  │  │  Query Service   │  │     Qdrant       │  │     Ollama       │ │ │
│  │  │  ┌────────────┐  │  │  ┌────────────┐  │  │  ┌────────────┐  │ │ │
│  │  │  │ App        │  │  │  │ App        │  │  │  │ App        │  │ │ │
│  │  │  │ Container  │  │  │  │ Container  │  │  │  │ Container  │  │ │ │
│  │  │  └────────────┘  │  │  └────────────┘  │  │  └────────────┘  │ │ │
│  │  │  ┌────────────┐  │  │  ┌────────────┐  │  │  ┌────────────┐  │ │ │
│  │  │  │ Envoy      │  │  │  │ Envoy      │  │  │  │ Envoy      │  │ │ │
│  │  │  │ Sidecar    │◀─┼──┼─▶│ Sidecar    │  │  │  │ Sidecar    │  │ │ │
│  │  │  │ (mTLS)     │  │  │  │ (mTLS)     │  │  │  │ (mTLS)     │  │ │ │
│  │  │  └────────────┘  │  │  └────────────┘  │  │  └────────────┘  │ │ │
│  │  │  SPIFFE ID:      │  │  SPIFFE ID:      │  │  SPIFFE ID:      │ │ │
│  │  │  spiffe://dc1/   │  │  spiffe://dc1/   │  │  spiffe://dc1/   │ │ │
│  │  │  ns/rag-platform/│  │  ns/rag-platform/│  │  ns/rag-platform/│ │ │
│  │  │  svc/query-      │  │  svc/qdrant      │  │  svc/ollama      │ │ │
│  │  │  service         │  │                  │  │                  │ │ │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘ │ │
│  │                                                                      │ │
│  │  All communication encrypted with mTLS                              │ │
│  │  Authorization enforced by Service Intentions                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

## How It Works

### 1. Automatic Sidecar Injection

When a pod is created with the annotation `consul.hashicorp.com/connect-inject: "true"`, Consul automatically injects an Envoy sidecar proxy:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: query-service
spec:
  template:
    metadata:
      annotations:
        consul.hashicorp.com/connect-inject: "true"
        consul.hashicorp.com/connect-service: "query-service"
        consul.hashicorp.com/connect-service-port: "8000"
        consul.hashicorp.com/connect-service-upstreams: "qdrant:6333,ollama:11434"
```

**What happens:**
1. Consul Connect Injector webhook intercepts pod creation
2. Adds Envoy sidecar container to the pod
3. Configures init container to set up iptables rules
4. Redirects all traffic through Envoy proxy

### 2. SPIFFE Identity Assignment

Each service receives a unique SPIFFE ID based on its Kubernetes metadata:

**Format:**
```
spiffe://<datacenter>/ns/<namespace>/svc/<service-name>
```

**Examples:**
```
spiffe://dc1/ns/rag-platform/svc/query-service
spiffe://dc1/ns/rag-platform/svc/qdrant
spiffe://dc1/ns/rag-platform/svc/ollama
spiffe://dc1/ns/rag-platform/svc/ui
spiffe://dc1/ns/rag-platform/svc/ingest
```

### 3. Certificate Management

Certificates are issued by **Vault's PKI engine**, not by Consul's built-in CA.
Consul is configured with `connectCA.provider: vault`, which delegates signing to
Vault's `connect_inter` PKI mount.  Consul rotates the intermediate automatically.

**Certificate Lifecycle:**
1. **Issuance:** Envoy sidecar requests a SVID from Consul's Connect CA agent
2. **Signing:** Consul forwards the CSR to Vault PKI (`connect_inter` mount)
3. **Delivery:** Vault signs and returns the X.509 certificate with SPIFFE SAN
4. **Rotation:** Certificates automatically rotate before expiration (default: 72 hours)
5. **Revocation:** Certificates revoked when pod terminates

**Certificate chain:**
```
Vault PKI (connect_root)  — 10-year root CA, never directly issues leaf certs
  └─► Vault PKI (connect_inter)  — intermediate CA, Consul rotates this
        └─► SPIFFE SVID per service  — presented by Envoy sidecars for mTLS
```

**Certificate Properties:**
- **Validity:** 72 hours (configurable)
- **Subject Alternative Name (SAN):** SPIFFE ID URI
- **Key Usage:** Digital Signature, Key Encipherment
- **Extended Key Usage:** Server Auth, Client Auth

### 4. mTLS Communication Flow

**Example: query-service → qdrant**

1. **Outbound (query-service):**
   - App makes request to `localhost:6333` (upstream)
   - Envoy intercepts via iptables
   - Envoy establishes mTLS connection to qdrant's Envoy
   - Envoy presents query-service's certificate
   - Envoy validates qdrant's certificate

2. **Inbound (qdrant):**
   - Qdrant's Envoy receives mTLS connection
   - Envoy validates query-service's certificate
   - Envoy checks Service Intentions (authorization)
   - If allowed, Envoy forwards to qdrant app on `localhost:6333`

3. **Response:**
   - Qdrant responds to Envoy
   - Envoy encrypts response with mTLS
   - Query-service's Envoy decrypts and forwards to app

### 5. Service Intentions (Authorization)

Service Intentions define which services can communicate:

```yaml
apiVersion: consul.hashicorp.com/v1alpha1
kind: ServiceIntentions
metadata:
  name: query-service-to-qdrant
spec:
  destination:
    name: qdrant
  sources:
  - name: query-service
    action: allow
    description: "Allow query service to access Qdrant"
```

**Authorization Flow:**
1. Envoy receives connection request
2. Extracts SPIFFE ID from client certificate
3. Queries Consul for Service Intentions
4. Allows or denies based on policy
5. Logs decision for audit

**Default Policy:** Deny all (explicit allow required)

## Service Communication Matrix

| Source Service | Destination Service | Port  | Protocol | Allowed |
|----------------|---------------------|-------|----------|---------|
| ui             | query-service       | 8000  | HTTP     | ✅      |
| query-service  | qdrant              | 6333  | HTTP     | ✅      |
| query-service  | ollama              | 11434 | HTTP     | ✅      |
| ingest         | qdrant              | 6333  | HTTP     | ✅      |
| ingest         | ollama              | 11434 | HTTP     | ✅      |
| *              | *                   | *     | *        | ❌      |

## Configuration

### Consul Helm Values

Key configuration in `k8s/consul/consul-values.yaml`:

```yaml
global:
  name: consul
  datacenter: dc1

  # Required for OpenShift — handles SCC for all Consul pods automatically
  openshift:
    enabled: true

  # Delegate certificate signing to Vault PKI instead of Consul's built-in CA.
  # Consul calls Vault's connect_inter mount to sign each SPIFFE SVID.
  connectCA:
    provider: "vault"
    vaultConfig:
      address: "http://vault.vault.svc.cluster.local:8200"
      token: "root"
      rootPKIPath: "connect_root"
      intermediatePKIPath: "connect_inter"

# Consul Connect sidecar injection
connectInject:
  enabled: true
  default: false   # Opt-in per service via annotation
```

### Service Annotations

**Basic Service:**
```yaml
annotations:
  consul.hashicorp.com/connect-inject: "true"
  consul.hashicorp.com/connect-service: "qdrant"
  consul.hashicorp.com/connect-service-port: "6333"
```

**Service with Upstreams:**
```yaml
annotations:
  consul.hashicorp.com/connect-inject: "true"
  consul.hashicorp.com/connect-service: "query-service"
  consul.hashicorp.com/connect-service-port: "8000"
  consul.hashicorp.com/connect-service-upstreams: "qdrant:6333,ollama:11434"
```

**Upstream Format:** `<service-name>:<local-port>[:<datacenter>]`

### Service Intentions

**Allow Specific Service:**
```yaml
apiVersion: consul.hashicorp.com/v1alpha1
kind: ServiceIntentions
metadata:
  name: ui-to-query-service
spec:
  destination:
    name: query-service
  sources:
  - name: ui
    action: allow
```

**Deny All (Default):**
```yaml
apiVersion: consul.hashicorp.com/v1alpha1
kind: ServiceIntentions
metadata:
  name: deny-all
spec:
  destination:
    name: "*"
  sources:
  - name: "*"
    action: deny
```

## Security Features

### 1. Mutual TLS (mTLS)

- **Encryption:** All service-to-service traffic encrypted
- **Authentication:** Both client and server verify certificates
- **Authorization:** Service Intentions enforce access control
- **Audit:** All connections logged with SPIFFE IDs

### 2. Certificate Rotation

- **Automatic:** Certificates rotate before expiration
- **Zero Downtime:** New certificates issued while old ones valid
- **Revocation:** Immediate revocation on pod termination

### 3. Identity-Based Security

- **SPIFFE IDs:** Cryptographically verifiable identities
- **No Secrets:** No need to manage service credentials
- **Dynamic:** Identities tied to Kubernetes ServiceAccounts

### 4. Network Segmentation

- **Intentions:** Fine-grained access control
- **Default Deny:** Explicit allow required
- **Namespace Isolation:** Services isolated by namespace

## Observability

### Metrics

Envoy proxies expose Prometheus metrics:

```
# Connection metrics
envoy_cluster_upstream_cx_total
envoy_cluster_upstream_cx_active
envoy_cluster_upstream_cx_connect_fail

# Request metrics
envoy_cluster_upstream_rq_total
envoy_cluster_upstream_rq_time
envoy_cluster_upstream_rq_xx (2xx, 4xx, 5xx)

# TLS metrics
envoy_listener_ssl_handshake
envoy_ssl_connection_error
```

### Logs

**Envoy Access Logs:**
```json
{
  "start_time": "2026-03-30T07:00:00.000Z",
  "method": "GET",
  "path": "/ask",
  "response_code": 200,
  "bytes_sent": 1234,
  "duration": 45,
  "upstream_service": "qdrant",
  "downstream_remote_address": "10.128.0.5:45678",
  "x_forwarded_for": null,
  "user_agent": "python-requests/2.31.0",
  "request_id": "abc-123-def-456",
  "authority": "query-service:8000",
  "upstream_host": "10.128.0.6:6333"
}
```

### Consul UI

Access Consul UI to view:
- Service topology
- Service Intentions
- Certificate status
- Connection metrics

```bash
# Port-forward Consul UI
oc port-forward -n consul svc/consul-ui 8500:80

# Access at http://localhost:8500
```

## Troubleshooting

### Sidecar Not Injected

**Check annotations:**
```bash
oc get pod <pod-name> -n rag-platform -o yaml | grep consul.hashicorp.com
```

**Check Connect Injector logs:**
```bash
oc logs -n consul -l app=consul-connect-injector
```

### mTLS Connection Failures

**Check Envoy logs:**
```bash
oc logs <pod-name> -c envoy-sidecar -n rag-platform
```

**Common issues:**
- Certificate expired
- Service Intention denies connection
- Upstream service not registered

### Service Intention Not Working

**Verify intention exists:**
```bash
oc get serviceintentions -n rag-platform
```

**Check Consul logs:**
```bash
oc logs -n consul -l app=consul
```

### Certificate Issues

**Check certificate validity:**
```bash
# Exec into Envoy sidecar
oc exec -it <pod-name> -c envoy-sidecar -n rag-platform -- sh

# View certificate
cat /consul/connect-inject/service.crt | openssl x509 -text -noout
```

## Best Practices

1. **Use Service Intentions:** Always define explicit allow rules
2. **Monitor Metrics:** Track connection failures and latency
3. **Rotate Certificates:** Use short TTLs (default 72h is good)
4. **Test Intentions:** Verify authorization before production
5. **Enable Audit Logs:** Log all connection attempts
6. **Use Namespaces:** Isolate services by namespace
7. **Limit Upstreams:** Only declare required upstreams

## Production Considerations

### High Availability

- Deploy 3+ Consul servers
- Use persistent storage for Consul data
- Enable Consul snapshots for backup

### Performance

- Tune Envoy buffer sizes
- Adjust connection pool settings
- Monitor proxy resource usage
- Use connection keep-alive

### Security

- Enable Consul ACLs
- Use external CA (Vault PKI)
- Implement network policies
- Enable audit logging
- Rotate Consul gossip encryption key

### Monitoring

- Collect Envoy metrics in Prometheus
- Create Grafana dashboards
- Set up alerts for:
  - Certificate expiration
  - Connection failures
  - High latency
  - Intention denials

## References

- Consul Connect: https://www.consul.io/docs/connect
- SPIFFE Specification: https://spiffe.io/docs/latest/spiffe-about/overview/
- Envoy Proxy: https://www.envoyproxy.io/docs/envoy/latest/
- Service Mesh Patterns: https://www.consul.io/docs/connect/observability
