#!/bin/bash
set -euo pipefail

# Configure Vault for the RAG platform on OpenShift.
#
# Role: Vault is the PKI backend for Consul's Connect CA.
# Consul delegates SPIFFE certificate signing to Vault's PKI engine.
# Services never receive a Vault token — they get SPIFFE SVIDs from
# Consul, which are signed by Vault's connect_inter mount.
#
# Two-tier PKI hierarchy:
#   connect_root   — 10-year root CA, never issues leaf certs directly
#   connect_inter  — intermediate CA; Consul rotates this periodically
#
# Consul authenticates to Vault via the Kubernetes auth method,
# using the consul-server / consul-client ServiceAccount tokens.

echo "Configuring Vault..."

# Wait for Vault to be ready
echo "Waiting for Vault to be ready..."
for i in {1..30}; do
  if vault status >/dev/null 2>&1; then
    echo "✓ Vault is ready"
    break
  fi
  if [ $i -eq 30 ]; then
    echo "❌ Vault did not become ready in time"
    exit 1
  fi
  sleep 2
done

# ── Kubernetes auth ──────────────────────────────────────────────────────────
echo "Enabling Kubernetes auth..."
vault auth enable kubernetes 2>/dev/null || echo "  (already enabled)"

vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc:443"

echo "✓ Kubernetes auth configured"

# ── PKI: root CA ─────────────────────────────────────────────────────────────
echo "Setting up Vault PKI (connect_root)..."
vault secrets enable -path=connect_root pki 2>/dev/null || echo "  (already enabled)"
vault secrets tune -max-lease-ttl=87600h connect_root

vault write connect_root/root/generate/internal \
  common_name="Consul Connect Root CA" \
  ttl=87600h \
  issuer_name=consul-root \
  >/dev/null 2>&1 || echo "  (root CA already exists)"

vault write connect_root/config/urls \
  issuing_certificates="${VAULT_ADDR}/v1/connect_root/ca" \
  crl_distribution_points="${VAULT_ADDR}/v1/connect_root/crl"

echo "✓ connect_root ready"

# ── PKI: intermediate CA ──────────────────────────────────────────────────────
echo "Setting up Vault PKI (connect_inter)..."
vault secrets enable -path=connect_inter pki 2>/dev/null || echo "  (already enabled)"
vault secrets tune -max-lease-ttl=8760h connect_inter

vault write connect_inter/config/urls \
  issuing_certificates="${VAULT_ADDR}/v1/connect_inter/ca" \
  crl_distribution_points="${VAULT_ADDR}/v1/connect_inter/crl"

echo "✓ connect_inter ready"

# ── Policy: Consul needs full control of both PKI mounts ─────────────────────
vault policy write consul-connect-ca - <<'EOF'
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
EOF

echo "✓ consul-connect-ca policy created"

# ── Kubernetes auth roles for Consul ─────────────────────────────────────────
vault write auth/kubernetes/role/consul-server \
  bound_service_account_names=consul-server \
  bound_service_account_namespaces=consul \
  policies=consul-connect-ca \
  ttl=1h

vault write auth/kubernetes/role/consul-client \
  bound_service_account_names=consul-client \
  bound_service_account_namespaces=consul \
  policies=consul-connect-ca \
  ttl=1h

echo "✓ Kubernetes auth roles for consul-server and consul-client created"
echo ""
echo "✓ Vault configuration complete"
echo "  PKI mounts:  connect_root  connect_inter"
echo "  Policy:      consul-connect-ca"
echo "  K8s roles:   consul-server  consul-client (namespace: consul)"
