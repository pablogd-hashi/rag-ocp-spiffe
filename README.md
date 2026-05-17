# RAG Platform — Qdrant Learning Lab

A RAG platform that answers natural-language questions about platform
engineering docs. Built on Qdrant Cloud — used as a structured learning
lab for Qdrant's core concepts: collections, HNSW, quantization,
multi-tenancy, hybrid search, and retrieval evaluation.

Blog: [Designing a RAG Platform in OpenShift](https://medium.com/@pablogd/designing-a-rag-platform-in-openshift-35df0937684c?postPublishedType=repub)

---

## How Qdrant works — the essentials

**Collection** — the primary namespace. Holds a set of points with a fixed
vector dimension and distance metric (cosine, dot, euclidean). Created once;
dimension and distance cannot change. The HNSW index lives inside it.

**Point** — the unit of storage. Three parts:
- `id` — unique integer or UUID within the collection
- `vector` — float32 array of fixed dimension, or a `Document` for server-side embedding
- `payload` — arbitrary JSON (text, source, tenant, doc_type, …)

**Cloud Inference** — instead of embedding locally, pass
`Document(text="...", model="sentence-transformers/all-minilm-l6-v2")` as
the vector. Qdrant Cloud embeds it server-side before executing the upsert
or query. No local GPU or Ollama needed for embedding.

**HNSW** — the ANN index inside each collection. A graph where each node
(point) connects to its `m` nearest neighbors. Queries traverse the graph
from a random entry point toward the query vector. Tunable:
- `m` — connections per node (default 16). Higher = better recall, more RAM.
- `ef_construct` — search width at index build time (default 100). Set once.
- `hnsw_ef` — search width at query time. Tune per request, no rebuild needed.

**Payload index** — an inverted index on a payload field. Mandatory for
efficient filtering. Without it, a `tenant=X` filter scans all N payload
documents per query (O(N)). With it: O(log N). Create with
`create_payload_index(field_name="tenant", field_schema=PayloadSchemaType.KEYWORD)`.

**Quantization** — compresses float32 vectors to reduce RAM:
- Scalar INT8: 4x compression, <2% recall loss with rescore
- Binary: 32x compression, requires rescore to maintain acceptable recall

> Full reference: [`docs/architecture/qdrant-client-reference.md`](docs/architecture/qdrant-client-reference.md)

---

## How this RAG pipeline works

```
docs/ (.md, .hcl, .tf)
    │
    ▼  chunker.py splits files into 200–500 token chunks
    │
    ▼  QdrantClient(cloud_inference=True)
       PointStruct(vector=Document(text=chunk, model="all-minilm-l6-v2"))
       → Qdrant Cloud embeds server-side → stores in HNSW index
    │
    ▼  User question
       qd.query_points(query=Document(text=question, model="all-minilm-l6-v2"))
       → Qdrant Cloud embeds → ANN search → top-5 chunks returned
    │
    ▼  Chunks passed as context to phi3 (Ollama) → grounded answer
```

Embedding model: `sentence-transformers/all-minilm-l6-v2` (dim=384, Qdrant Cloud)
LLM: `phi3` (local Ollama — only for answer generation, not embedding)
Collection: `platform-docs` (296 points, cosine distance)

---

## Quick start — Learning Lab

```bash
# 1. Prerequisites
brew install go-task                     # Taskfile runner
pip install uv                           # fast package manager

# 2. Credentials — copy and fill in
cp .env.example .env                     # add QDRANT_URL and QDRANT_API_KEY

# 3. Install core dependencies
task setup                               # qdrant-client, ollama, python-dotenv

# 4. Migrate docs to Qdrant Cloud (server-side embedding, no Ollama needed)
task w0:migrate

# 5. Verify the collection is healthy
task w0:explore

# 6. Start the interactive Lab UI
task lab                                 # opens at http://localhost:8502
```

---

## Weekly curriculum

Each week builds on the previous. Run experiments from the repo root.

| Week | Focus | Tasks |
|------|-------|-------|
| 0 | Collections, points, payloads, cloud inference | `task w0:explore` `task w0:ops` `task w0:filters` `task w0:migrate` |
| 1 | HNSW, distance metrics, quantization | `task w1:distance` `task w1:hnsw` `task w1:quant` |
| 2 | Embedding models, chunking, hybrid search | `task w2:embed` `task w2:hybrid` |
| 3 | Throughput benchmarks, retrieval eval (MRR@K) | `task w3:bench` `task w3:eval` |
| 4 | Multi-tenancy patterns, cross-encoder reranking | `task w4:tenants` `task w4:reranking` |

Week 2+ requires `task setup:ml` (adds fastembed + sentence-transformers ~500MB).

### What each week teaches

**Week 0 — Foundations**
- Connect to Qdrant Cloud, list and inspect collections
- Create collections with explicit HNSW config
- Upsert points with stable IDs (idempotent re-ingest via SHA-256)
- Create payload indexes for multi-tenant filtering
- Run a self-query test (retrieve a stored vector, re-query, verify top result = self)

**Week 1 — Index internals**
- Distance metric comparison on real embeddings (cosine vs euclid vs dot)
- HNSW recall@K vs latency across m={8,16,32,64} ef_construct={50,100,200,400}
- Runtime ef tuning: vary `hnsw_ef` per request with no index rebuild
- Scalar INT8 and binary quantization: memory reduction vs recall tradeoff

**Week 2 — Embeddings and hybrid search**
- Compare all-minilm-l6-v2 (384-dim) vs bge-small-en (384-dim) on the same corpus
- Chunking strategies: fixed-size vs sentence-boundary vs paragraph
- Hybrid search: dense (HNSW cosine) + sparse (BM25) fused with RRF

**Week 3 — Observability and evaluation**
- Concurrent query benchmark: P50/P95/P99 latency at 1/5/10/20 threads
- MRR@5 ground truth evaluation at multiple hnsw_ef values
- When MRR@5 is low: debug with exact=True to separate chunking from HNSW issues

**Week 4 — Production patterns**
- Pattern A vs Pattern B multi-tenancy: isolation, performance, operational cost
- Cross-encoder reranking: HNSW retrieves top-20, cross-encoder reranks to top-5
- When to use reranking: precision-critical queries where latency budget allows

---

## Task reference

```bash
# Cloud / setup
task setup            # install core deps (no fastembed/onnxruntime)
task setup:ml         # install ML deps (fastembed + sentence-transformers ~500MB)
task cloud:status     # check connection + collection stats

# Week 0
task w0:migrate       # ingest docs/ to Qdrant Cloud (server-side embedding)
task w0:recover       # delete all broken collections and verify cluster health
task w0:explore       # list collections, show HNSW config, run self-query test
task w0:ops           # collection CRUD, payload indexes, distance metric reference
task w0:filters       # payload filtering and multi-tenancy simulation

# Week 1
task w1:distance      # cosine vs euclidean vs dot product comparison
task w1:hnsw          # HNSW m/ef_construct recall vs latency benchmark
task w1:quant         # scalar vs binary quantization memory and recall

# Week 2 (requires task setup:ml)
task w2:embed         # embedding model comparison (all-minilm vs bge)
task w2:hybrid        # hybrid dense+sparse search (BM25 + embeddings)

# Week 3
task w3:bench         # concurrent query throughput benchmark
task w3:eval          # retrieval quality eval with MRR@K

# Week 4 (requires task setup:ml for reranking)
task w4:tenants       # multi-tenancy: payload filter vs per-collection
task w4:reranking     # cross-encoder reranking (two-stage retrieval)

# Lab UI
task lab              # Streamlit multi-page app at http://localhost:8502
task lab:install      # install lab-ui dependencies
```

---

## Project layout

```
docs/
  architecture/
    qdrant-client-reference.md   ← every Qdrant Python call explained
    consul-connect-spiffe.md
    openshift-deployment.md
  configuration/
  runbooks/
  policies/

weeks/
  week-0/
    setup/        migrate_to_cloud.py, recover_collection.py
    experiments/  01_connect_and_explore.py, 02_collection_ops.py, 03_payload_filters.py
  week-1/         01_distance_metrics.py, 02_hnsw_tuning.py, 03_quantization.py
  week-2/         01_embedding_comparison.py, 02_chunking_strategies.py, 03_hybrid_search.py
  week-3/         01_benchmark.py, 02_retrieval_eval.py
  week-4/         01_multitenancy.py, 02_reranking.py
  requirements-core.txt     # weeks 0-1 + notebook (no onnxruntime)
  requirements-ml.txt       # weeks 2+ (adds fastembed + sentence-transformers)

notebooks/
  qdrant_lab.ipynb           # VS Code notebook, follow-along with Qdrant UI

lab-ui/                      # Streamlit multi-page interactive UI
  app.py
  pages/
  lib/qdrant_helpers.py

ingest/                      # Docker ingest pipeline (uses cloud inference)
query-service/               # FastAPI /ask endpoint (Ollama for LLM, cloud inference for embed)
ui/                          # Streamlit chat interface (original demo)
k8s/                         # OpenShift / Kubernetes manifests
scripts/                     # Vault PKI setup, walkthrough, ask helper
Taskfile.yml
docker-compose.yml
```

---

## Environment variables

```bash
# .env — gitignored, never commit
QDRANT_URL=https://<cluster-id>.europe-west3-0.gcp.cloud.qdrant.io
QDRANT_API_KEY=<your-api-key>

EMBED_MODEL=sentence-transformers/all-minilm-l6-v2   # Qdrant Cloud inference
EMBED_DIM=384

OLLAMA_URL=http://localhost:11434   # only needed for LLM (phi3), not for embedding
LLM_MODEL=phi3

COLLECTION=platform-docs
TOP_K=5
TENANT=default
```

---

## Original OpenShift demo

The original demo deploys a zero-trust service mesh with Vault PKI + Consul
Connect mTLS + SPIFFE. All service-to-service traffic is encrypted and
authenticated — no API keys between services.

```bash
task setup:ocp     # start CRC, build images, deploy Vault + Consul
task demo:ocp      # deploy RAG platform, ingest docs, run demo
task status:ocp    # check all pods and routes
task clean:ocp     # remove all resources
```

**Certificate chain:**
```
Vault connect_root  (root CA)
  └─► Vault connect_inter  (intermediate, rotated by Consul)
        └─► SPIFFE SVID per service  (72h, Envoy sidecar)
```

**Example questions for the original demo:**
```
What are the recovery steps when Consul loses quorum during a leader election?
What Vault paths does the PKI policy allow for certificate issuance?
What SPIFFE identity format does Consul Connect assign to services?
What is the procedure for rotating the Vault master key?
How does Consul Connect use Vault PKI to issue SPIFFE certificates?
```

![Architecture](images/Architecture_flow.png)
![Streamlit UI](images/Streamlit_ui.png)
