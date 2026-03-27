# Vault PKI policy — grants platform services the ability to request
# short-lived TLS certificates from the intermediate CA while keeping
# root CA operations restricted to the security team.

# Allow services to issue certificates from the intermediate CA.
path "pki_int/issue/platform-dot-internal" {
  capabilities = ["create", "update"]
}

# Allow reading the intermediate CA certificate chain (needed by Envoy
# sidecars to validate peer certificates).
path "pki_int/cert/ca_chain" {
  capabilities = ["read"]
}

# Allow listing issued certificates for audit purposes.
path "pki_int/certs" {
  capabilities = ["list"]
}

# Allow reading individual certificate details (serial lookup).
path "pki_int/cert/+" {
  capabilities = ["read"]
}

# Allow services to read their own SPIFFE-compatible certificate.
path "pki_int/issue/spiffe-platform" {
  capabilities = ["create", "update"]
}

# Deny all access to the root CA.
path "pki/root/*" {
  capabilities = ["deny"]
}

# Deny revoking the intermediate CA — only the security team can do this.
path "pki_int/root/sign-intermediate" {
  capabilities = ["deny"]
}

# Allow reading the CRL distribution point (required for revocation checks).
path "pki_int/crl" {
  capabilities = ["read"]
}

# Allow tidying up expired certificates (automated cleanup job).
path "pki_int/tidy" {
  capabilities = ["create", "update"]
}
