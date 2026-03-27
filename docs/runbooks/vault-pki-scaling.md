# Vault PKI at Scale — Operational Runbook

## Overview

This runbook covers the operational patterns for running Vault's PKI secrets
engine at enterprise scale. It addresses CA hierarchy design, certificate
issuance performance, revocation management, and Vault Agent integration for
automated certificate delivery.

Reference: HashiCorp Validated Design — Vault Operating Guide for Scaling.

---

## CA Hierarchy Design

### Three-Tier Architecture

Deploy a three-tier PKI hierarchy for production:

1. **Root CA** — offline, managed via HSM or air-gapped system. The root CA
   should never directly issue leaf certificates. Its sole purpose is to sign
   intermediate CA certificates.

2. **Vault Signing CA** — intermediate CA mounted in the admin namespace. Signs
   subordinate issuing CAs for each tenant. Recommended key type: EC P-384
   with a TTL of 5–7 years staggered from the root.

3. **Tenant Issuing CAs** — per-namespace intermediate CAs in tenant namespaces.
   These issue leaf certificates to applications. Key type: EC P-256.

### Namespace Isolation

Mount a dedicated PKI secrets engine in each tenant namespace. This provides
strong CA isolation boundaries and supports multi-tenancy without cross-tenant
certificate visibility.

---

## Certificate Issuance

### Role Configuration

Define a PKI role per team that constrains issuance:

```
vault write pki/roles/team-a \
  allowed_domains="tenant-1.example.com" \
  allow_subdomains=true \
  allow_bare_domains=false \
  allow_wildcard_certificates=false \
  allow_ip_sans=false \
  enforce_hostnames=true \
  server_flag=true \
  client_flag=false \
  key_type="ec" \
  key_bits=256 \
  max_ttl="720h" \
  no_store=true \
  generate_lease=false
```

Key settings:

- **`no_store=true`** — critical for performance. Performance standby nodes
  can issue certificates independently, yielding a 3–5x improvement in leaf
  issuance rates because Vault skips writing the certificate to storage.
- **`generate_lease=false`** — avoids lease tracking overhead.
- **`max_ttl="720h"`** — 30-day maximum. Short-lived certificates (≤30 days)
  minimize reliance on CRL infrastructure.

### Issuance Endpoints

| Endpoint | Key generation | Use case |
|----------|---------------|----------|
| `pki/issue/<role>` | Vault generates key + cert | Automated workflows, Vault Agent |
| `pki/sign/<role>` | Client submits CSR | Traditional CA model, client retains key |

The `issue` endpoint is required when using Vault Agent templating because the
agent needs access to both the private key and the certificate.

### Templated Policies for Dynamic Access

Use identity-templated policies to avoid per-team policy definitions:

```hcl
path "pki/issue/{{identity.entity.metadata.TeamName}}" {
  capabilities = ["update"]
}
```

Pre-create entities with metadata (`TeamName`, `AppName`, `TLSDomain`) and bind
them to auth method aliases.

---

## Performance Optimisation

### Seal Wrapping

When managed keys (HSM/KMS) are not available for the issuing CA, enable seal
wrapping. This encrypts CA private keys using the seal device before writing
them to storage, adding a layer beyond standard keyring encryption.

### Managed Keys for the Signing CA

Use PKCS#11 integration to store the central signing CA key material in an HSM
or cloud KMS. Signing latency increases, so apply managed keys only to the
signing CA — let Vault manage issuing CA keys directly with EC curves to
minimise latency impact.

---

## Revocation Management

### CRL Configuration

Enable automatic CRL rebuilding to prevent unexpected expiration:

```
auto_rebuild = true
```

Do not enable immediate CRL rebuilding on every revocation — this is
expensive at scale. Instead, rely on the automatic rebuild interval and
trigger manual rebuilds only for urgent revocations.

### OCSP

OCSP provides real-time certificate status and is preferred over CRLs in
high-volume environments. Configure Authority Information Access (AIA) URLs
per issuer to ensure cluster-consistent responses.

### Cross-Cluster Revocation

When using performance replication, synchronise revocation data across
clusters. Configure AIA and CRL Distribution Point URLs per issuer so that
unified CRLs and OCSP responses are available across the entire topology.

### Tidying

Run the tidy operation every 24 hours on all tenant PKI mounts:

- Cleans expired certificates, issuers, and revocation metadata.
- Improves query performance and long-term scalability.

---

## Vault Agent Certificate Delivery

### pkiCert vs secret

| Function | Behaviour |
|----------|-----------|
| `pkiCert` | Checks filesystem first; renews only when expired or past threshold. Recommended. |
| `secret` | Always fetches a new certificate on startup. Less efficient. |

### Renewal Threshold

```hcl
template_config {
  lease_renewal_threshold = 0.75
}
```

Default is 90% of certificate lifetime. Set to 0.75 (75%) for a more
conservative renewal window.

### Template Example

```
{{- with pkiCert "pki/issue/team-a"
    "common_name=app.tenant-1.example.com"
    "ttl=14d"
    "remove_roots_from_chain=true" -}}
{{ .Key | writeToFile "/certs/private.key" "" "" "0600" }}
{{ .Cert | writeToFile "/certs/server.crt" "" "" "0644" }}
{{- range .CAChain }}
{{ . | writeToFile "/certs/server.crt" "" "" "0644" "append" }}
{{- end }}
{{- end }}
```

### Post-Render Reload

```hcl
template {
  source      = "/vault-agent/pkiCerts.tmpl"
  destination = "/vault-agent/template-output/pki.pem"
  exec {
    command = ["nginx", "-s", "reload"]
  }
}
```

Deploy Vault Agent in daemon mode (`exit_after_auth = false`) and manage it
via systemd for automatic restarts.

### Certificate Metadata (Vault 1.17+)

Attach base64-encoded JSON metadata at issuance time for tracking even when
`no_store=true` is set. Metadata is stored separately from the certificate
itself.

---

## Sentinel Policy Enforcement

Use Sentinel to enforce that the certificate common name matches the
requesting entity's metadata:

```python
precond = rule {
  request.operation in ["write", "update"] and
  strings.has_prefix(request.path, "pki/issue")
}

main = rule when precond {
  request.data.common_name matches identity.entity.metadata.TLSDomain
}
```

---

## Monitoring Checklist

- Certificate issuance frequency via audit logs.
- Renewal failure alerts — especially for short-lived certificates.
- Certificate expiration alerts for critical systems.
- Capacity planning dashboards tracking issuance trends.
- Version-control all PKI role and policy configurations (Terraform preferred).
