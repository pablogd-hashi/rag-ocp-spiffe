# Least-privilege read policy for the RAG platform secrets.
# Bound to the "rag-reader" AppRole role.

path "kv/data/rag/config" {
  capabilities = ["read"]
}

path "kv/metadata/rag/config" {
  capabilities = ["read"]
}
