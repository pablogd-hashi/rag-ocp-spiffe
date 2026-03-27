{{- with secret "kv/data/rag/config" -}}
export QDRANT_API_KEY="{{ .Data.data.qdrant_api_key }}"
export QDRANT_URL="{{ .Data.data.qdrant_url }}"
export OLLAMA_URL="{{ .Data.data.ollama_url }}"
{{- end }}
