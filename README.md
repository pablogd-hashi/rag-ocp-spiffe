# rag-hashicorp-platform

A RAG (Retrieval-Augmented Generation) platform that indexes platform engineering documents — runbooks, Vault policies, Terraform files, architecture docs — into a vector database and answers natural-language questions about them.

The model only reasons over documents you control. It cannot invent answers that are not in the index. That constraint is the point: answers are auditable and scoped to your actual documentation.

---

## How it works

The system runs in two phases.

**Ingest** reads every `.md`, `.hcl`, and `.tf` file under `docs/`, splits each into semantically coherent chunks (headings for Markdown, top-level blocks for HCL), and converts each chunk into a vector embedding using `nomic-embed-text`. Those vectors are stored in Qdrant alongside the original text.

**Query** takes a natural-language question, embeds it the same way, finds the most similar chunks by vector distance, and passes them to the language model as context. The model generates an answer grounded in what was retrieved.

```
docs/
  runbooks/          ──► ingest ──► Qdrant (vectors + text)
  policies/                              │
  architecture/                          │
  jobs/                                  ▼
                              question ──► embed ──► search ──► phi3 ──► answer
```

When ingest runs you will see output like:

```
✓ Created collection 'platform-docs'
  architecture/consul-connect-spiffe.md: 52 chunk(s)
  runbooks/consul-leader-election.md: 18 chunk(s)
  ...
```

Each line is one document. The number of chunks is how many passages were embedded and written to Qdrant. More chunks means more surface area the retriever can match against.

**Models**

| Role | Model | Size |
|---|---|---|
| Embeddings | `nomic-embed-text` | 274 MB |
| Chat | `phi3` | 2.2 GB |

---

## Deployment options

| Mode | When to use |
|---|---|
| Docker Compose | Local development and demos |
| OpenShift (CRC) | Production-ready with Vault + Consul mTLS + SPIFFE |

---

## Docker Compose

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| [Ollama](https://ollama.com) | any | Run natively — **not** in Docker. On Apple Silicon this uses Metal GPU, giving 10–50x faster inference than a container. |
| Docker Desktop | 4.x+ | Only needs ~4 GB — Ollama is no longer in the compose stack. |
| [Task](https://taskfile.dev) | any | |

Make sure Ollama is running before `task demo`:

```bash
ollama serve   # or launch Ollama.app
```

### Run

```bash
task demo:docker
```

This pulls the Ollama models, ingests all documents under `docs/`, starts the services, opens the Streamlit UI in your browser, and drops you into the interactive demo.

| Service | URL |
|---|---|
| Streamlit UI | http://localhost:8501 |
| Query API | http://localhost:8000/docs |

### Interactive demo

The demo asks you to choose a mode:

```
  1) Guided tour  — 6 questions covering the full corpus
  2) Ask your own — free-form Q&A (type 'quit' to exit)
  3) Both
```

You can also run it separately at any time:

```bash
task walkthrough
```

### Ask a single question

```bash
task ask -- "Which runbook covers a Consul leader election failure?"
# or just: task ask   (prompts for input)
```

**Example questions the index can answer:**

- What are the recovery steps when Consul loses quorum during a leader election?
- What Vault paths does the PKI policy allow for certificate issuance?
- What SPIFFE identity format does Consul Connect assign to services in the mesh?
- What is the procedure for rotating the Vault master key and what are the risks?
- What metrics should I monitor to detect replication lag between Vault primary and secondary?
- What is the difference between Vault Transit encryption and Transform tokenization?
- How does Consul Connect use Vault PKI to issue SPIFFE certificates instead of its built-in CA?
- What is the difference between the connect_root and connect_inter PKI mounts?
- How does Consul authenticate to Vault to sign certificates?
- How do Service Intentions enforce deny-by-default between services?
- What happens to PKI performance when the no_store option is enabled?
- What CRC resource requirements does this platform need to run?

### Other commands

```bash
task status          # check running services
task logs -- ui      # stream logs for a service
task down            # stop containers
task clean           # stop containers and delete volumes
```

### Add your own documents

Place `.md`, `.hcl`, or `.tf` files anywhere under `docs/` and re-run ingest:

```bash
docker compose run --rm ingest
```

The chunker splits Markdown on heading boundaries and HCL on top-level block boundaries so semantic units are never bisected.

---

## OpenShift (CRC)

Deploys the full platform on OpenShift Local with a complete zero-trust service mesh:

- **Vault** — PKI backend. Runs two PKI mounts (`connect_root`, `connect_inter`) that act as the Certificate Authority for the entire mesh. Nothing else — no KV secrets, no VSO.
- **Consul Connect** — mTLS between every service, enforced by Envoy sidecars automatically injected into each pod.
- **SPIFFE** — cryptographic workload identity. Each service gets a certificate of the form `spiffe://dc1/ns/rag-platform/svc/<name>`, signed by Vault's PKI engine. Services authenticate each other with these certificates — no API keys or passwords.
- **Service Intentions** — explicit allow-list. Services not declared in `k8s/consul/intentions.yaml` cannot communicate, regardless of network reachability.

Services connect to each other via their Envoy sidecar (on `localhost`). Envoy handles the mTLS handshake, certificate verification, and intention check before forwarding traffic.

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| [OpenShift Local (CRC)](https://developers.redhat.com/products/openshift-local/overview) | 2.x+ | Local OpenShift cluster |
| `oc` CLI | 4.x+ | OpenShift commands |
| `helm` | 3.x+ | Consul install |
| `vault` CLI | 1.15+ | Vault PKI configuration |
| Docker Desktop | 4.x+ | Building app images |
| [Task](https://taskfile.dev) | any | Automation |

**CRC resource requirements:** 20 GB RAM, 6 CPU cores, 80 GB disk.

### Run

```bash
task demo:ocp     # start CRC, build images, deploy everything, open demo
```

Or in two steps if you want to inspect infrastructure before deploying the RAG platform:

```bash
task setup:ocp    # start CRC, build images, deploy Vault + Consul
task demo:ocp     # deploy RAG platform, ingest docs, open demo
```

| Service | URL |
|---|---|
| Streamlit UI | `https://ui-rag-platform.apps-crc.testing` |
| Vault UI | `http://localhost:8200` (token: `root`) |
| Consul UI | `http://localhost:8500` (port-forward started automatically) |
| OpenShift console | `https://console-openshift-console.apps-crc.testing` |

In the **Vault UI** you can browse the `connect_root` and `connect_inter` PKI mounts and see the CA certificates that sign every SPIFFE SVID in the mesh.

In the **Consul UI** you can see the service topology, the mTLS connection graph, and the Service Intentions that control which services are allowed to talk to each other.

### Other commands

```bash
task status:ocp                      # check all services
task logs:ocp -- query-service       # stream logs
task clean:ocp                       # remove all resources
task stop:ocp                        # stop CRC
```

### Certificate flow

```
Vault PKI (connect_root)            — 10-year root CA, visible in Vault UI
  └─► Vault PKI (connect_inter)     — intermediate CA, rotated by Consul
        └─► SPIFFE SVID per service — issued to each Envoy sidecar
              └─► mTLS handshake    — verified on every service call
```

---

## Project structure

```
docs/                   documents indexed by the RAG pipeline
ingest/                 embedding + ingest pipeline (Python)
query-service/          FastAPI /ask endpoint
ui/                     Streamlit interface
k8s/
  base/                 Kubernetes manifests (Qdrant, Ollama, services)
  vault/                Vault dev server deployment
  consul/               Consul Helm values + service intentions
scripts/
  configure-vault.sh    Vault PKI + Kubernetes auth setup
  walkthrough.sh        guided demo queries
  ask.sh                single-question CLI helper
Taskfile.yml            all automation tasks
docker-compose.yml      local development stack
```
