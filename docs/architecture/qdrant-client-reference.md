# Qdrant Python Client — Line-by-Line Reference

This document explains every Qdrant Python client construct used in this
project. Each section covers what the call does, why the specific arguments
are chosen, what happens inside Qdrant when you call it, and how to answer
about it in an interview.

---

## 1. Connecting to Qdrant Cloud

```python
from qdrant_client import QdrantClient

qd = QdrantClient(
    url="https://<cluster-id>.europe-west3-0.gcp.cloud.qdrant.io",
    api_key="eyJ...",
    cloud_inference=True,
    timeout=30,
    check_compatibility=False,
)
```

### `url`
The full HTTPS endpoint of your Qdrant Cloud cluster. Qdrant Cloud assigns
one URL per cluster. All REST and gRPC traffic goes here. The port is
implicit (443 for HTTPS). The URL encodes the cluster ID and GCP region.

### `api_key`
A JWT token scoped to your cluster. Qdrant Cloud creates one per API key
entry in the web console. It is sent as the `api-key` HTTP header on every
request. Treat it like a password — never commit it. In this project it
lives in `.env` which is gitignored.

### `cloud_inference=True`
Tells the Python client to route `Document(text=..., model=...)` objects to
Qdrant's inference API endpoint before the vector operation. Without this
flag, passing a `Document` object as a vector raises a validation error.

What happens server-side: Qdrant Cloud runs the embedding model (e.g.
`sentence-transformers/all-minilm-l6-v2`) in its own inference cluster,
returns the float32 vector, and then executes the upsert or query against
the HNSW index. You never see the float32 — the round-trip is internal to
Qdrant Cloud.

Why this matters in an interview: "We removed local Ollama from the embedding
path. Qdrant Cloud handles embedding server-side. The network cost is one
HTTPS call that combines embed + search. Latency is lower than
client-embed→client-query because there is no second network hop."

### `timeout=30`
Maximum seconds to wait for a single HTTP response. Qdrant Cloud is remote —
a slow query or a large upsert batch can take several seconds. 30 seconds is
safe for interactive use. Production ingest pipelines should use 60–120.

### `check_compatibility=False`
The Python client checks the server's Qdrant version on first connection and
raises a warning (or error) if the server is newer than the client expects.
This project runs qdrant-client 1.16.1 against Qdrant Cloud server 1.18.x.
The flag suppresses the warning. The API is backwards-compatible, so the
mismatch is safe.

Interview trap: "Why not just upgrade the client?" Python 3.9 cannot install
qdrant-client ≥ 1.16.2 because those versions require Python ≥ 3.10. This
project runs on Python 3.9.

---

## 2. Cloud Inference — the Document object

```python
from qdrant_client.models import Document

# Used in PointStruct (ingest):
vector=Document(text=chunk, model="sentence-transformers/all-minilm-l6-v2")

# Used in query_points (search):
query=Document(text="How does Vault PKI work?", model="sentence-transformers/all-minilm-l6-v2")
```

`Document` is a wrapper that says "embed this text server-side before using
it as a vector." It is not a float32 array — it is a lazy embedding request.

### `text`
The raw string to embed. For ingest: a document chunk (typically 200–500
tokens). For search: the user's question. The model tokenizes this string
and returns a float32 vector of the model's output dimension.

### `model`
Identifies which embedding model Qdrant Cloud should use. The model name is
a Hugging Face model ID. Qdrant Cloud maintains a set of supported models.

`sentence-transformers/all-minilm-l6-v2`:
- Output dimension: 384
- Produces L2-normalized vectors (magnitude = 1.0)
- Fast, general-purpose, good for English text
- For cosine distance: all-minilm is a solid baseline

The model must match the dimension of the collection. If the collection was
created with `size=384`, every vector upserted must be 384-dimensional.
Passing a Document that embeds to 768 dimensions into a 384-dim collection
raises a `400 Bad Request: Vector dimension error`.

Interview question: "How would you change the embedding model?"
1. Delete the collection (vectors are tied to a specific dimension)
2. Create a new collection with the new model's output dimension
3. Re-ingest all documents with the new model
There is no in-place migration of vectors — the vector space changes.

---

## 3. Collections

### Check existence

```python
qd.collection_exists("platform-docs")  # returns True or False
```

Makes a lightweight GET request. Uses this before create or delete to avoid
racing on an already-present collection. Does not raise on missing collection.

### Create a collection

```python
from qdrant_client.models import Distance, VectorParams

qd.create_collection(
    collection_name="platform-docs",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
)
```

**`collection_name`**: The namespace for this set of vectors. All operations
target a named collection. Qdrant does not have schemas across collections —
each collection is independent.

**`VectorParams(size=384, distance=Distance.COSINE)`**:
- `size=384`: The fixed dimension of every vector in this collection. Set
  once at creation. Cannot be changed without deleting the collection.
  Must match the embedding model's output dimension exactly.
- `distance=Distance.COSINE`: The similarity metric used during ANN search.

#### Distance metrics — when to use each

`Distance.COSINE` — measures the angle between vectors, ignores magnitude.
Best for text embeddings where direction encodes semantics. Formula:
`similarity = dot(A,B) / (|A| × |B|)`. Score range: -1 to 1 (higher = more
similar). For unit-normalized vectors (like all-minilm), cosine = dot product.

`Distance.DOT` — raw dot product without length normalization. For
unit-normalized vectors, identical to cosine. Prefer for OpenAI models
(they document dot product as the recommended metric even though vectors are
normalized). Use when magnitude carries information (e.g., importance-weighted
docs with longer = higher magnitude).

`Distance.EUCLID` — straight-line L2 distance. Score: 0 = identical, higher
= less similar (inverted from cosine). Use for un-normalized vectors such
as raw image features or audio embeddings where magnitude is meaningful.

Interview question: "Does the distance metric matter for text RAG?"
For unit-normalized vectors: no, the ranking is identical (they are monotonic
transforms of each other). The score values differ but orderings do not.
Choose cosine because it's self-documenting and universally understood.

### HNSW configuration at creation time

```python
from qdrant_client.models import HnswConfigDiff

qd.create_collection(
    collection_name="w1-bench",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(m=32, ef_construct=200),
)
```

**`m`** (default 16): Number of bi-directional connections each node has in
the HNSW graph. Higher m = denser graph = better recall = more RAM and slower
ingest. Memory impact: `n_points × m × 2 × 8 bytes` for the graph edges.

**`ef_construct`** (default 100): During index build, how many candidate
nodes Qdrant explores when placing a new point into the graph. Higher =
better neighbor selection = higher recall = slower ingest. Does NOT affect
query-time behavior — only the quality of the graph structure built.

You can only set m and ef_construct at collection creation. Changing them
requires recreating the collection.

**`full_scan_threshold`** (default 10000): Collections with fewer points than
this skip HNSW entirely and do a brute-force scan. For small collections this
is faster. For the platform-docs collection (296 points), Qdrant may use
brute-force regardless of HNSW config.

### Delete a collection

```python
qd.delete_collection("platform-docs")
```

Permanently removes all vectors, payloads, and indexes. Instant. No undo.
In this project: used during recovery (broken collections) and before
re-ingestion to ensure a clean state.

### Inspect a collection

```python
info = qd.get_collection("platform-docs")

info.points_count           # total points upserted
info.indexed_vectors_count  # points in the HNSW graph (may lag points_count)
info.segments_count         # number of segments (grows during ingest, shrinks after optimize)
info.optimizer_status       # "ok" | "optimizing" | "error"
info.config.params.vectors.size      # vector dimension
info.config.params.vectors.distance  # Distance enum
info.config.params.hnsw_config.m     # connections per node
info.config.params.hnsw_config.ef_construct
info.payload_schema         # dict of indexed payload fields
```

`indexed_vectors_count < points_count` means the HNSW optimizer is still
processing recent upserts. Queries still work (Qdrant searches both indexed
and unindexed segments) but may be slower.

`optimizer_status`: Qdrant's background optimizer continuously merges small
segments into larger immutable ones and rebuilds the HNSW graph. During
heavy ingest you will see `optimizer_status = optimizing`. Queries run
concurrently with optimization.

---

## 4. Payload Indexes

```python
from qdrant_client.models import PayloadSchemaType

qd.create_payload_index(
    collection_name="platform-docs",
    field_name="tenant",
    field_schema=PayloadSchemaType.KEYWORD,
)
```

This is one of the most interview-critical calls in the codebase.

**What a payload index does**: Without an index, filtering on `tenant` during
a query scans ALL payload documents — O(N). With a KEYWORD index, Qdrant
builds an inverted index over the `tenant` field: O(log N + results).

**PayloadSchemaType.KEYWORD**: Best for exact string matching — tenant IDs,
doc_type labels, tags. Other index types:
- `INTEGER`: sorted index for range queries (`timestamp > X`)
- `FLOAT`: sorted index for numeric ranges
- `TEXT`: full-text tokenized index for substring/fuzzy search within payload
- `BOOL`: bitmap index

**Create indexes BEFORE ingesting data**: Qdrant can index already-present
payloads after the fact, but doing it before ingest is cheaper — there is no
retroactive re-indexing cost.

**Why this is mandatory for multi-tenancy**: If you store 100k chunks for 50
tenants and filter by `tenant` without a payload index, every query scans
all 100k payloads regardless of how many results match. With the index, only
the matching tenant's payloads are visited.

Interview question: "What happens if you forget the payload index?"
The system still works but filters degrade to O(N) scans. At 100k points
with a tenant filter, latency is proportional to collection size rather than
result count. You will not get an error — just slow queries. The Qdrant UI
shows which fields are indexed under Collection → Payload.

---

## 5. Points and Upsert

```python
from qdrant_client.models import PointStruct

points = [
    PointStruct(
        id=_stable_id(chunk, source),
        vector=Document(text=chunk, model=EMBED_MODEL),
        payload={
            "text":     chunk,
            "source":   rel,
            "tenant":   TENANT,
            "doc_type": dtype,
        },
    )
    for chunk in chunks
]

qd.upsert(collection_name="platform-docs", points=points)
```

### `PointStruct.id`
Every point has a unique integer or UUID string ID within the collection.
In this project IDs are deterministic SHA-256 hashes:

```python
def _stable_id(text: str, source: str) -> int:
    h = hashlib.sha256(f"{source}::{text[:200]}".encode()).hexdigest()
    return int(h[:16], 16) % (2**63)
```

Why deterministic? **Idempotent ingest.** Running `upsert` twice on the
same document produces the same ID — the second upsert is a no-op update,
not a duplicate. Without stable IDs, every run of the ingest pipeline
creates duplicate vectors for the same content.

`% (2**63)` keeps the integer within Qdrant's signed 64-bit ID range.

### `PointStruct.vector`
The vector representation of this point. Can be:
- `list[float]` — a pre-computed float32 array
- `Document(text=..., model=...)` — sent to cloud inference first

### `PointStruct.payload`
Arbitrary JSON. Stored alongside the vector. Returned in query results.
Used for filtering and for surfacing the original text in the response.

Key payload fields this project uses:
- `text` — the raw chunk text (returned to the LLM as context)
- `source` — relative file path (shown to the user as a source citation)
- `tenant` — used for multi-tenant payload filtering
- `doc_type` — architecture / runbook / policy / configuration / general

### `qd.upsert`
Inserts new points and overwrites existing points with the same ID. Safe to
call multiple times. Batching in 100-point batches avoids request timeouts
on large corpora:

```python
batch_size = 100
for i in range(0, len(points), batch_size):
    qd.upsert(collection_name=COLLECTION, points=points[i : i + batch_size])
```

A single upsert call with 296 points and cloud inference would require
Qdrant to embed all 296 chunks in one HTTP request. Batches of 100 are
safe for timeout limits and allow progress reporting.

---

## 6. Querying

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue, SearchParams

results = qd.query_points(
    collection_name="platform-docs",
    query=Document(text=req.question, model=EMBED_MODEL),
    query_filter=Filter(must=[
        FieldCondition(key="tenant", match=MatchValue(value="platform"))
    ]),
    limit=5,
    with_payload=True,
    search_params=SearchParams(hnsw_ef=128),
).points
```

### `query`
What to search for. Either:
- `list[float]` — a pre-embedded query vector (same dim as the collection)
- `Document(text=..., model=...)` — cloud inference embeds it first

### `query_filter`
Applied BEFORE the ANN search (pre-filtering). Only points matching the
filter are considered as candidates. The filter uses the payload index to
find matching point IDs efficiently, then ANN searches only those points.

**`Filter(must=[...])`**: All conditions in `must` must match (logical AND).
Other filter types: `should` (OR), `must_not` (NOT).

**`FieldCondition(key="tenant", match=MatchValue(value="platform"))`**:
Exact match on a KEYWORD-indexed field. Returns only points where
`payload["tenant"] == "platform"`. Without the payload index on `tenant`,
this scans all payloads — see section 4.

### `limit`
Maximum number of results to return. ANN search finds approximately the top
`limit` nearest neighbors. Set `limit = TOP_K` in normal queries. Set
`limit = TOP_K * 4` when doing two-stage retrieval with a cross-encoder
reranker (retrieve more candidates, rerank, return top K).

### `with_payload=True`
Include the full payload dict in every result. If False, only `id` and
`score` are returned. Set True when you need the `text` field to pass to
the LLM, or the `source` field to show citations.

### `with_vectors=True`
Include the raw float32 vector in results. Used in the self-query test in
`01_connect_and_explore.py` to retrieve a stored vector and re-query with
it. Expensive — only use for debugging.

### Result fields

```python
for r in results:
    r.id       # point ID (int or str)
    r.score    # cosine similarity: 0.0 (unrelated) to 1.0 (identical)
    r.payload  # dict — same structure as upserted payload
    r.vector   # list[float] only if with_vectors=True
```

A `score` above ~0.7 indicates strong semantic similarity for cosine distance
on all-minilm. Below 0.4 usually means the query is not well-covered by the
corpus — either the question is off-domain or the chunking is wrong.

### `SearchParams` — tuning recall and latency at query time

```python
SearchParams(
    hnsw_ef=128,   # query-time search width (default: collection's ef_construct)
    exact=False,   # False = HNSW ANN, True = brute-force exact scan
    quantization=QuantizationSearchParams(rescore=True),
)
```

**`hnsw_ef`**: At query time, the HNSW algorithm explores `hnsw_ef` candidate
nodes when traversing the graph. Higher = more accurate (approaches exact
search) = slower. Default is the collection's `ef_construct` value.

This is the cheapest tuning knob: you can vary it per request with no rebuild:
```python
# Fast interactive query
SearchParams(hnsw_ef=32)

# High-stakes compliance query
SearchParams(hnsw_ef=512)
```

**`exact=True`**: Bypasses HNSW entirely. Computes similarity against every
single vector in the collection. Always returns the true top-K nearest
neighbors. Slow (O(N) × dim), but useful as ground truth for recall benchmarks:

```python
hnsw_results  = qd.query_points(..., search_params=SearchParams(exact=False)).points
exact_results = qd.query_points(..., search_params=SearchParams(exact=True)).points
recall = len({r.id for r in hnsw_results} & {r.id for r in exact_results}) / K
```

---

## 7. Scrolling (Browsing Without a Query)

```python
points, next_offset = qd.scroll(
    collection_name="platform-docs",
    scroll_filter=Filter(must=[...]),  # optional
    limit=20,
    offset=None,          # pass next_offset from previous call to paginate
    with_payload=True,
    with_vectors=False,
)
```

`scroll` returns points in storage order, not by similarity. No query vector
needed. Used for:
- Verifying payloads after ingest (are `tenant` and `doc_type` correct?)
- Spot-checking chunk quality
- Paginating through an entire collection
- Retrieving a stored vector for a self-query test

**Pagination**: `scroll` returns `(points, next_offset)`. Pass `next_offset`
as `offset` in the next call to get the following page. When `next_offset`
is None, you have reached the end of the collection.

---

## 8. Quantization

```python
from qdrant_client.models import (
    ScalarQuantization, ScalarQuantizationConfig, ScalarType,
    BinaryQuantization, BinaryQuantizationConfig,
    QuantizationSearchParams,
)

# Scalar INT8 — 4x compression
qd.create_collection(
    collection_name="w1-quant-scalar",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            always_ram=True,
        )
    ),
)

# Binary — 32x compression
qd.create_collection(
    collection_name="w1-quant-binary",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    quantization_config=BinaryQuantization(
        binary=BinaryQuantizationConfig(always_ram=True)
    ),
)
```

**Scalar INT8**: Each float32 (4 bytes) compressed to int8 (1 byte) using
per-vector min-max scaling. Qdrant stores the scale factor and offset to
reconstruct approximate float32 values for rescoring. Memory: 4x reduction.

**Binary**: Each float32 → 1 bit (positive = 1, negative = 0 after centering
to mean). Hamming distance for fast comparison. Memory: 32x reduction. High
recall loss without rescoring — always enable rescore for binary.

**`always_ram=True`**: Keep the quantized vectors in RAM even if the original
float32 vectors are on disk. The quantized vectors are used for the fast
first-pass ANN search; having them in RAM keeps latency low.

### Rescoring at query time

```python
results = qd.query_points(
    collection_name="w1-quant-binary",
    query=Document(text=q, model=EMBED_MODEL),
    limit=K,
    search_params=SearchParams(
        exact=False,
        quantization=QuantizationSearchParams(rescore=True),
    ),
).points
```

**Rescore flow**: ANN searches on compressed (quantized) vectors to get top
`oversampling_factor × K` candidates fast → recomputes exact float32
similarity for those candidates → returns true top K.

`rescore=True` recovers most recall lost to compression. Without rescore,
binary quantization loses ~20–40% recall. With rescore: typically <2% loss.

Memory layout with quantization + rescore:
- float32 vectors: on disk (or RAM if collection fits)
- quantized vectors: always in RAM (`always_ram=True`)
- ANN search: uses quantized (fast, cheap)
- Rescore: fetches float32 for final ranking (disk I/O only for top ~50 candidates)

---

## 9. Multi-Collection Management

```python
# List all collections
cols = qd.get_collections().collections
for c in cols:
    print(c.name)

# Safe probe — get_collection returns 500 if the collection is broken
try:
    info = qd.get_collection(c.name)
    print(f"OK: {info.points_count} points")
except Exception as e:
    print(f"BROKEN: {e.__class__.__name__}")
```

`get_collections().collections` returns a list of `CollectionDescription`
objects. Each has only `.name`. Use `get_collection(name)` for full stats.

Qdrant Cloud can have a collection in a broken state (e.g. after a WAL
replay failure during a crash-recovery cycle). `get_collections()` still
lists it, but `get_collection(name)` raises `UnexpectedResponse` with HTTP
500. The only recovery is `delete_collection(name)` followed by re-ingest.

---

## 10. Practical Interview Cheat Sheet

**"What is a Qdrant collection?"**
A collection is the primary namespace in Qdrant. It holds a set of points
(vectors + payloads) with a fixed vector dimension and distance metric. Think
of it as a typed table: once you set dimension and distance at creation, they
cannot change. All HNSW index structures live inside one collection.

**"How does HNSW work?"**
HNSW (Hierarchical Navigable Small World) is a graph-based ANN index. Each
point is a node. During index build, each new point connects to its m nearest
neighbors in the graph. Querying starts at the top layer (sparse, long-range
connections), greedily navigates toward the query vector, then descends into
denser layers. `ef_construct` controls how many candidates are examined when
placing each new node — higher = better graph = better recall = slower build.
At query time, `hnsw_ef` controls the search width — the number of candidates
examined before returning results.

**"Why is a payload index mandatory for multi-tenancy?"**
Without it, Qdrant must read every payload document to evaluate the filter
condition. With a KEYWORD index, Qdrant maintains an inverted index of
(field_value → list of point IDs). The filter lookup is O(log N), not O(N).
A tenant filter on 100k points without an index reads 100k payload documents
on every query regardless of tenant size.

**"What is the difference between hnsw_ef_construct and hnsw_ef?"**
`ef_construct` is set at index build time and controls graph quality — higher
means each node has better neighbors selected for it. It cannot be changed
without rebuilding the index. `hnsw_ef` (also called `ef` at query time) is
set per query and controls search width during retrieval — higher means
Qdrant explores more candidate paths before returning results. You can tune
`hnsw_ef` per request with no index rebuild, making it the cheapest quality
knob available.

**"What happens when you upsert with the same ID twice?"**
The second upsert overwrites the first. Vector, payload, and all fields are
replaced. This is why deterministic IDs (based on content hash) make
re-ingestion idempotent — re-running the ingest pipeline produces exactly the
same set of point IDs, and upsert becomes a no-op for unchanged content.

**"When would you choose Pattern A (shared collection) vs Pattern B (per-tenant
collection) for multi-tenancy?"**
Pattern A (one collection, payload filter): simpler ops, one HNSW graph
shared across tenants, lower RAM cost. Mandatory: payload index on tenant
field. Best when tenants have similar document volumes and <100 tenants.
Pattern B (one collection per tenant): complete isolation, delete a tenant
by deleting its collection, can tune HNSW per tenant. Cost: N × HNSW RAM,
N collections to monitor. Best for enterprise SaaS with strict data isolation
requirements or when tenant counts are large and varied in size.

**"What is cloud inference and why did you choose it?"**
Qdrant Cloud's inference API embeds text server-side before executing the
vector operation. The Python client sends `Document(text=..., model=...)` in
place of a float32 array. Qdrant Cloud embeds it and runs the query or upsert
in one server-side operation. We chose it to eliminate local Ollama from the
embedding path — local embedding with nomic-embed-text was the bottleneck in
the ingest pipeline. With cloud inference, ingest is I/O-bound (reading docs)
rather than compute-bound (running transformer inference locally).

**"How would you debug a query that returns wrong results?"**
1. Check `retrieval_top_score` — if top score is below 0.5, the query is
   semantically outside the corpus. Add more docs or check chunking.
2. Run with `exact=True` — if exact search also returns junk, the problem is
   embedding quality or chunking, not HNSW approximation.
3. Check `indexed_vectors_count` vs `points_count` — if the gap is large,
   the optimizer is still building the HNSW graph. Wait or force-optimize.
4. Check filter correctness — if using `tenant` filter, verify the payload
   index exists and the filter value matches exactly.
5. Inspect the actual chunks with `scroll` — look at what text was upserted.
   Short, mid-sentence chunks score poorly on unrelated queries.
