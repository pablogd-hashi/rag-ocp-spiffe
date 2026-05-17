# Week 0 — Cloud Connection, Collections, Payloads, and Filters

**Goal:** Get Qdrant Cloud wired up, understand what a collection actually is
under the hood, and make every point queryable by tenant and doc_type.

**Time:** 2–3 days  
**Prerequisites:** `.env` filled in, Ollama running locally, `docs/` populated

---

## What you will be able to explain after this week

- What a Qdrant collection stores vs what a relational table stores
- What a point is (id + vector + payload) and why the payload is not indexed by default
- What a payload index does and what happens at query time without one
- How the Qdrant Cloud dashboard maps to the API responses you see in code
- The difference between `scroll` (browse, no query) and `query_points` (ANN search)
- Why idempotent point IDs matter for re-ingestion

---

## Mental model: Qdrant data model

```
Collection
├── HNSW Index          (the graph that makes ANN search fast)
├── Segment 0           (an immutable SSTable-like chunk of points)
│   ├── Point { id: 42, vector: [0.1, 0.3, ...], payload: {text, source, tenant} }
│   └── ...
├── Segment 1
└── WAL (Write-Ahead Log — ensures durability before segments are flushed)
```

A **segment** is the unit of storage. When you upsert points they land in a
mutable "appendable" segment. The **optimizer** (background goroutine) merges
small segments into larger immutable ones and builds/rebuilds the HNSW graph.
This is why `indexed_vectors_count` can be less than `points_count` right
after a large ingest — indexing is asynchronous.

---

## Setup

```bash
# Load env vars
export $(cat .env | xargs)

# Verify connection
python weeks/week-0/experiments/01_connect_and_explore.py

# Migrate existing docs to cloud with enhanced payloads
python weeks/week-0/setup/migrate_to_cloud.py

# Or use the Taskfile
task w0:explore
task w0:migrate
```

---

## Labs

See `LABS.md` for step-by-step exercises with expected outputs.

---

## Interview talking points from this week

1. **"What is a Qdrant collection?"**
   It is a named namespace that holds points sharing the same vector
   dimensionality and distance metric. Unlike a relational table, every point
   carries an arbitrary JSON payload. The schema of that payload is inferred
   lazily — there is no DDL. The HNSW index is per-collection.

2. **"What happens if I don't create a payload index?"**
   Qdrant will still filter correctly, but it does a full scan of all payload
   values at query time. On 100k points with a string filter, that is ~100k
   comparisons per query. With a keyword payload index, it is an inverted-index
   lookup — O(log N + results). For production tenant isolation, payload indexes
   are not optional.

3. **"Why are your point IDs deterministic hashes?"**
   Upsert semantics: if a point with the same ID already exists, it is
   overwritten. Deterministic IDs make re-ingestion idempotent — you can run
   the ingest job repeatedly without accumulating duplicates. Without this, each
   run appends new points and your collection grows without bound.

4. **"What does `indexed_vectors_count` less than `points_count` mean?"**
   The optimizer is still building the HNSW graph for recently upserted points.
   Those points exist in Qdrant and are searchable (via brute-force fallback),
   but are not yet in the ANN graph. Query recall may be slightly lower until
   indexing completes. Watch `optimizer_status` to confirm.
