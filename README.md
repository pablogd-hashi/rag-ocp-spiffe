# rag-hashicorp-platform

A RAG platform that answers natural-language questions about platform engineering docs — runbooks, Vault policies, architecture docs. The model only reasons over documents you index.

---

## How it works

**Ingest** reads every `.md` and `.yaml` file under `docs/`, splits them into chunks, and embeds each chunk with `nomic-embed-text`. Vectors are stored in Qdrant.

**Query** embeds your question the same way, finds the nearest chunks, and passes them to `phi3` as context to generate a grounded answer.

```
docs/ ──► embed ──► Qdrant
                       │
     question ──► embed ──► search ──► phi3 ──► answer
```

---

## Docker (local)

Runs Vault, Consul, Qdrant, the query service, and the UI. Ollama runs natively on your machine (not in Docker) so it can use the GPU.

**Prerequisites:** [Ollama](https://ollama.com), Docker Desktop, [Task](https://taskfile.dev)

```bash
ollama serve          # must be running first
task demo             # pull models, ingest docs, start services, open UI
```

| Service | URL |
|---|---|
| UI | http://localhost:8501 |
| Query API | http://localhost:8000/docs |
| Vault UI | http://localhost:8200 — token: `root` |
| Consul UI | http://localhost:8500 |

```bash
task ask              # ask a question (prompts for input)
task ask -- "What are the recovery steps when Consul loses quorum?"
task ingest           # re-ingest after adding docs
task clean            # stop and remove volumes
```

Drop `.md` or `.yaml` files anywhere under `docs/` and run `task ingest` to add your own content.

---

## OpenShift (CRC)

Deploys the full platform with a zero-trust service mesh:

- **Vault** acts as the Certificate Authority. It runs two PKI mounts (`connect_root`, `connect_inter`) that sign every SPIFFE certificate in the mesh.
- **Consul Connect** injects an Envoy sidecar into every pod. All service-to-service traffic is mTLS — encrypted and authenticated by SPIFFE certificates issued by Vault.
- **Service Intentions** enforce a deny-by-default policy. Only explicitly allowed service pairs can communicate.

No API keys or passwords exist between services. Authentication is the mTLS handshake.

**Prerequisites:** `oc`, `helm`, `vault` CLI, Docker Desktop, [Task](https://taskfile.dev)

**CRC requirements:**

- OpenShift Local (CRC) 4.18 — the Consul version used (1.21.x / consul-k8s 1.8.x) is certified for OCP 4.16–4.18
- 20 GB RAM, 6 CPU cores, 80 GB disk

Download the OCP 4.18 bundle from the Red Hat CRC release page and configure CRC to use it:

```bash
crc config set bundle ~/.crc/cache/crc_vfkit_4.18.2_arm64.crcbundle
```

```bash
task setup:ocp        # start CRC, build images, deploy Vault + Consul
task demo:ocp         # deploy RAG platform, ingest docs, run demo
```

| Service | URL |
|---|---|
| UI | `https://ui-rag-platform.apps-crc.testing` |
| Vault UI | http://localhost:8200 — token: `root` |
| Consul UI | http://localhost:8500 |

```bash
task status:ocp       # check all pods and routes
task logs:ocp -- ui   # stream logs for a service
task clean:ocp        # remove all resources
task stop:ocp         # stop CRC
```

**Certificate chain:**
```
Vault connect_root  (root CA)
  └─► Vault connect_inter  (intermediate, rotated by Consul)
        └─► SPIFFE SVID per service  (72h, presented by Envoy)
```

---

## Example questions

```
What are the recovery steps when Consul loses quorum during a leader election?
What Vault paths does the PKI policy allow for certificate issuance?
What SPIFFE identity format does Consul Connect assign to services?
What is the procedure for rotating the Vault master key?
What metrics should I monitor for Vault replication lag?
What is the difference between Vault Transit encryption and Transform tokenization?
How does Consul Connect use Vault PKI to issue SPIFFE certificates?
What is the difference between connect_root and connect_inter?
How does Consul authenticate to Vault to sign certificates?
How do Service Intentions enforce deny-by-default between services?
```

---

## Project layout

```
docs/               indexed documents (runbooks, policies, architecture)
ingest/             embedding pipeline
query-service/      FastAPI /ask endpoint
ui/                 Streamlit interface
k8s/
  base/             deployments for Qdrant, Ollama, query-service, ui, ingest
  vault/            Vault dev server
  consul/           Consul Helm values + service intentions
scripts/
  configure-vault.sh   PKI setup (connect_root, connect_inter, Consul auth)
  walkthrough.sh       interactive demo
  ask.sh               single-question helper
Taskfile.yml
docker-compose.yml
```
