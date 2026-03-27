# Vault Transit and Transform Engines — Operational Runbook

## Overview

This runbook covers encryption-as-a-service using Vault's Transit secrets
engine and data protection using the Transform secrets engine (Enterprise ADP).
Transit handles high-throughput encrypt/decrypt/sign/verify operations.
Transform provides format-preserving encryption (FPE), tokenization, and data
masking for compliance-sensitive data.

Reference: HashiCorp Validated Design — Vault Operating Guide for Scaling.

---

## Transit Secrets Engine

### Design Properties

- **Centralised key management** — all encryption keys live in Vault. Single
  point of control for creation, rotation, and destruction.
- **No data storage** — Transit does not store plaintext or ciphertext. This
  makes operations primarily read-oriented and enables dynamic scaling.
- **High throughput** — optimised for thousands of transactions per second (TPS)
  with minimal latency.
- **Supported algorithms** — AES-256-GCM (default), ChaCha20-Poly1305,
  ED25519, RSA, ECDSA, and more.

### Key Rotation

Vault's key versioning maintains multiple key versions simultaneously.
Implement a rolling update strategy:

1. Rotate the key — new data is encrypted with the latest version.
2. Rewrap existing ciphertext to the new version in batches.
3. Once rewrapping is complete, set `min_decryption_version` to retire
   old versions.

Design applications with retry logic for temporary failures during rotation
windows. Test rotation procedures regularly in staging.

### Access Control

Issue short-lived tokens with appropriate TTL values for Transit operations.
Short TTLs reduce the risk window if a token is compromised.

### Scaling

Transit operations are read-oriented and do not write to storage. Add
non-voter (performance standby) nodes to increase throughput. Performance
replication secondaries can also absorb Transit load locally.

---

## Transform Secrets Engine

Requires Vault Enterprise with the Advanced Data Protection (ADP) module.

### Format Preserving Encryption (FPE)

FPE encrypts data while preserving its original format. Algorithms: FF1, FF3-1.

#### Define a Template

```bash
vault write transform/template/ccn-template \
  type=regex \
  pattern='(\d{4})-(\d{4})-(\d{4})-(\d{4})' \
  alphabet=builtin/numeric
```

#### Create the Transformation

```bash
vault write transform/transformation/fpe-ccn \
  type=fpe \
  template=ccn-template \
  tweak_sources=internal \
  allowed_roles=payments
```

#### Encode

```bash
vault write transform/encode/payments \
  transformation=fpe-ccn \
  value=1111-2222-3333-4444
```

Output preserves the format: `9300-3376-4943-8903`.

Use cases: encrypting SSNs or credit card numbers while maintaining format
compatibility with legacy systems and databases that enforce format constraints.

### Tokenization

Replaces sensitive data with a token that has no exploitable meaning outside
Vault. The plaintext-to-token mapping is stored in Vault's internal storage
or an external SQL database.

```bash
vault write transform/encode/payments \
  value="123-45-6789" \
  transformation=us-ssn
```

Security considerations:

- Configure the token storage backend securely.
- Limit sensitive data processing (data minimisation).
- Enable audit logging for all tokenization operations.
- Ensure compliance alignment: PCI-DSS, GDPR, HIPAA.

### Data Masking

Partially obfuscates data for limited visibility:

```bash
vault write transform/encode/payments \
  value="+1 123-345-5678" \
  transformation=phone-number
```

Output: `+1 ###-###-####`.

### Roles

Bind transformations to roles:

```bash
vault write transform/role/payments \
  transformations=card-number,uk-passport,us-ssn
```

Policy:

```hcl
path "transform/encode/payments" {
  capabilities = ["create", "update"]
}
path "transform/decode/payments" {
  capabilities = ["create", "update"]
}
```

---

## Performance Tuning

### Batch Processing

Use batch API endpoints for bulk Transform operations. Batching significantly
reduces per-request overhead and improves overall throughput compared to
individual calls.

### Caching

Cache frequently encrypted values on the application side to reduce load on
Vault, especially when the same data requires repeated encryption.

### Connection Pooling

Reuse HTTP connections to minimise TCP handshake and TLS negotiation overhead.
Most Vault client libraries support this natively.

### External SQL for Tokenization

For high-volume tokenization, configure an external database (MySQL,
PostgreSQL, or SQL Server) to store plaintext-token mappings. This offloads
write-intensive operations from Vault's internal storage.

Optimise the external database:

- Index frequently queried fields.
- Implement replication and clustering for fault tolerance.
- Monitor query execution plans.

### Latency

Deploy Vault clusters geographically close to data sources. Use load balancers
to distribute tokenization requests across non-voter nodes.

---

## Scaling Checklist

1. Monitor CPU utilisation (average and peak), response times, and operations
   per second.
2. Set alert thresholds for latency and throughput degradation.
3. Optimise load balancer distribution across non-voter nodes.
4. Scale CPU and memory for cryptographic processing workloads.
5. Add performance replication secondaries to absorb regional load.
6. Use external SQL databases for high-frequency tokenization writes.
