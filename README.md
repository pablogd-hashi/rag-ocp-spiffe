# rag-hashicorp-platform

A RAG (Retrieval-Augmented Generation) platform that indexes internal platform
documents — Markdown runbooks, HCL policies, Terraform files — into a Qdrant
vector database and answers natural-language questions about them.

Production credential delivery uses **HashiCorp Vault** with AppRole auth.
Service-to-service communication is secured with **Consul Connect** mTLS using
SPIFFE-issued X.509 certificates.

> Companion repository for the blog post:
> *Operationalising a RAG Platform with Vault, SPIFFE, and Consul*.

---

## Architecture

```
┌──────────┐    ┌──────────────┐    ┌────────┐
│ Streamlit │───▶│ Query Service │───▶│ Qdrant │
│    UI     │    │  (FastAPI)   │    └────────┘
└──────────┘    │              │───▶┌────────┐
                │              │    │ Ollama │
                └──────────────┘    └────────┘
                       ▲
                ┌──────┴──────┐
                │   Ingest    │  (batch — reads ./docs)
                └─────────────┘
```

- **Embedding model:** `nomic-embed-text` (768 dimensions)
- **Chat model:** `mistral`
- **Vector store:** Qdrant with cosine similarity

---

## Prerequisites

| Tool             | Version |
|------------------|---------|
| Docker Desktop   | 4.x+    |
| Docker Compose   | v2      |
| Ollama           | 0.6+    |
| GNU Make or Task | any     |

> On Apple Silicon, both Ollama models run natively. The demo flow
> completes in under 3 seconds once models are loaded.

---

## Quick Start

```bash
# Clone the repo
git clone https://github.com/<you>/rag-hashicorp-platform.git
cd rag-hashicorp-platform

# One command does everything: pulls models, ingests sample docs, starts services
task demo        # or: make demo
```

Once complete, open **http://localhost:8501** and ask:

> *Which runbook covers a leader election failure?*

You should see a grounded answer with citations pointing to
`runbooks/consul-leader-election.md`.

---

## Manual Step-by-Step

If you prefer to run each stage yourself:

```bash
# 1. Start Ollama and pull models
task setup

# 2. Ingest the sample documents
task ingest

# 3. Start the query service and UI
task up
```

### Service URLs

| Service      | URL                        |
|--------------|----------------------------|
| Qdrant       | http://localhost:6333       |
| Query API    | http://localhost:8000       |
| Streamlit UI | http://localhost:8501       |

### Stop everything

```bash
task down       # stop containers
task clean      # stop containers AND delete volumes
```

---

## Adding Your Own Documents

1. Place `.md`, `.hcl`, or `.tf` files anywhere under `./docs/`.
2. Re-run the ingest pipeline:
   ```bash
   task ingest
   ```
3. The chunker automatically dispatches to the correct strategy:
   - **Markdown** → heading-aware splitting (H1/H2/H3 boundaries).
   - **HCL / Terraform** → top-level block-aware splitting (`resource`,
     `data`, `module`, etc.) so semantic blocks are never bisected.

---

## Understanding Retrieval Scores

The Streamlit UI displays the **top cosine similarity score** from Qdrant
for each query.

| Score   | Meaning                                                  |
|---------|----------------------------------------------------------|
| ≥ 0.5   | Good match — the answer is grounded in indexed content.  |
| < 0.5   | Weak match — the index may be stale, or the question is out of scope. Re-ingest or rephrase the query. |

This score is the primary signal for retrieval quality degradation in
production monitoring.

---

## Production: Vault and Consul

In production, plaintext environment variables are replaced by
Vault-managed secrets and Consul-enforced mTLS.

### Vault Agent Credential Delivery

The `vault/` directory contains a ready-to-use configuration:

| File          | Purpose                                                   |
|---------------|-----------------------------------------------------------|
| `policy.hcl`  | Least-privilege read policy for `kv/data/rag/config`.    |
| `agent.hcl`   | Vault Agent with AppRole auth; renders secrets to disk.  |
| `config.tpl`  | Consul Template that renders env vars from KV.           |
| `setup.sh`    | Idempotent bootstrap: enables KV v2, writes placeholders, creates the AppRole role. |

**Flow:**

1. Run `vault/setup.sh` against your Vault cluster to bootstrap secrets
   and the AppRole role.
2. Deploy `vault/agent.hcl` as a sidecar. It authenticates with AppRole,
   fetches secrets from `kv/data/rag/config`, and renders them to
   `/vault/secrets/config.env`.
3. The application sources `/vault/secrets/config.env` at startup.
4. When secrets rotate, Vault Agent re-renders the file and sends
   `SIGHUP` to the main process for a zero-downtime reload.

### Consul Connect and SPIFFE

Consul Connect provides mTLS between all services using SPIFFE-compatible
X.509 SVIDs. Each service receives a certificate with a SPIFFE ID of the
form:

```
spiffe://<trust-domain>/ns/default/dc/dc1/svc/<service-name>
```

Intentions (L4/L7 authorization policies) restrict which services can
communicate. For example, the query service can reach Qdrant and Ollama,
but the UI can only reach the query service.

The Nomad job spec in `docs/jobs/rag-ingest.hcl` demonstrates the full
pattern: `vault` stanza for credential delivery, `connect` sidecar for
mTLS, and `template` block for rendering secrets into the task
environment.

> See the companion blog post for a detailed walkthrough of this
> architecture.

---

## Project Structure

```
rag-hashicorp-platform/
├── ingest/              # Batch ingestion pipeline
│   ├── ingest.py        # Main entry point
│   ├── chunker.py       # Markdown + HCL chunking strategies
│   ├── requirements.txt
│   └── Dockerfile
├── query-service/       # FastAPI /ask endpoint
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── ui/                  # Streamlit Q&A interface
│   ├── ask.py
│   ├── requirements.txt
│   └── Dockerfile
├── vault/               # Production credential delivery
│   ├── policy.hcl
│   ├── agent.hcl
│   ├── config.tpl
│   └── setup.sh
├── docs/                # Document corpus
│   ├── runbooks/        # Markdown runbooks
│   ├── policies/        # Vault HCL policies
│   └── jobs/            # Nomad HCL job specs
├── docker-compose.yml
├── Taskfile.yml
├── Makefile
├── .env.example
└── .gitignore
```

---

## License

MIT
