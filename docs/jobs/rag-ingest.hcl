job "rag-ingest" {
  datacenters = ["dc1"]
  type        = "batch"

  meta {
    description = "Ingest platform documents into the Qdrant vector store."
    owner       = "platform-engineering"
  }

  group "ingest" {
    count = 1

    restart {
      attempts = 2
      interval = "10m"
      delay    = "30s"
      mode     = "fail"
    }

    vault {
      policies    = ["rag-reader"]
      change_mode = "restart"
    }

    network {
      mode = "bridge"
    }

    service {
      name = "rag-ingest"
      connect {
        sidecar_service {
          proxy {
            upstreams {
              destination_name = "qdrant"
              local_bind_port  = 6333
            }
            upstreams {
              destination_name = "ollama"
              local_bind_port  = 11434
            }
          }
        }
      }
    }

    task "ingest" {
      driver = "docker"

      config {
        image = "ghcr.io/example-org/rag-ingest:latest"
        volumes = [
          "local/docs:/docs:ro",
        ]
      }

      template {
        data = <<-EOT
          QDRANT_URL=http://{{ env "NOMAD_UPSTREAM_ADDR_qdrant" }}
          QDRANT_API_KEY={{ with secret "kv/data/rag/config" }}{{ .Data.data.qdrant_api_key }}{{ end }}
          OLLAMA_URL=http://{{ env "NOMAD_UPSTREAM_ADDR_ollama" }}
          EMBED_MODEL=nomic-embed-text
          COLLECTION=platform-docs
          DOCS_PATH=/docs
        EOT
        destination = "secrets/env.env"
        env         = true
      }

      artifact {
        source      = "s3::https://s3.amazonaws.com/platform-docs/latest.tar.gz"
        destination = "local/docs"
      }

      resources {
        cpu    = 500
        memory = 1024
      }
    }
  }
}
