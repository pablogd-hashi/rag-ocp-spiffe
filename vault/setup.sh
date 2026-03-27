#!/usr/bin/env bash
# Idempotent Vault bootstrap for the RAG platform.
# Run against a Vault instance that is already unsealed.
set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR

echo "→ Enabling KV v2 secrets engine at kv/ ..."
vault secrets enable -path=kv -version=2 kv 2>/dev/null || echo "  (already enabled)"

echo "→ Writing placeholder secrets to kv/rag/config ..."
vault kv put kv/rag/config \
  qdrant_api_key="changeme" \
  qdrant_url="http://qdrant:6333" \
  ollama_url="http://ollama:11434"

echo "→ Writing policy 'rag-reader' ..."
vault policy write rag-reader /vault/policy.hcl 2>/dev/null \
  || vault policy write rag-reader "$(dirname "$0")/policy.hcl"

echo "→ Enabling AppRole auth method ..."
vault auth enable approle 2>/dev/null || echo "  (already enabled)"

echo "→ Creating AppRole role 'rag-reader' ..."
vault write auth/approle/role/rag-reader \
  token_policies="rag-reader" \
  token_ttl=1h \
  token_max_ttl=4h \
  secret_id_ttl=24h

echo ""
echo "=== Credentials ==="
ROLE_ID=$(vault read -field=role_id auth/approle/role/rag-reader/role-id)
SECRET_ID=$(vault write -field=secret_id -f auth/approle/role/rag-reader/secret-id)
echo "ROLE_ID:   ${ROLE_ID}"
echo "SECRET_ID: ${SECRET_ID}"
echo ""
echo "Store these in your deployment system (Nomad variables, K8s secrets, etc.)."
