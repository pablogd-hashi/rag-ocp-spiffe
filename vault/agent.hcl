# Vault Agent configuration for the RAG platform.
# Uses AppRole auth and renders secrets via Consul Template.

pid_file = "/tmp/vault-agent.pid"

vault {
  address = "https://vault.service.consul:8200"
}

auto_auth {
  method "approle" {
    mount_path = "auth/approle"
    config = {
      role_id_file_path   = "/vault/role-id"
      secret_id_file_path = "/vault/secret-id"
      remove_secret_id_file_after_reading = false
    }
  }

  sink "file" {
    config = {
      path = "/vault/token"
    }
  }
}

template {
  source      = "/vault/config.tpl"
  destination = "/vault/secrets/config.env"

  # Signal the main process when secrets are re-rendered so it can
  # reload configuration without a full restart.
  command = "kill -HUP $(cat /tmp/app.pid) 2>/dev/null || true"
}
