# Week 1 — Qdrant Internals: HNSW, Distance Metrics, Quantization, Segments

**Goal:** Understand how Qdrant stores, indexes, and searches vectors at the
architecture level. After this week you should be able to answer "why would
HNSW recall degrade?" and "what would you tune first for a latency-sensitive
workload?" without looking anything up.

**Time:** 4–5 days  
**Prerequisites:** Week 0 complete, collection populated in Qdrant Cloud

---

## Core concepts this week

### HNSW — How Qdrant's index actually works

HNSW (Hierarchical Navigable Small World) is a graph-based ANN index. It builds
a layered graph where:
- **Layer 0** contains ALL points, densely connected
- **Layer 1+** contain progressively fewer points (randomly promoted), sparsely connected

A query starts at the top layer (fewest nodes), greedily navigates toward the
query vector, descends to the next layer at the nearest node found, and repeats
until Layer 0. The final result set comes from Layer 0 traversal.

```
Layer 2  •————•                     (a few "highway" nodes)
              |
Layer 1  •——•——•——•——•              (more nodes, denser)
              |
Layer 0  •—•—•—•—•—•—•—•—•—•       (all points, start here for refinement)
                    ↑ query lands here, walks to nearest neighbors
```

**Parameters you control:**
| Param | Default | Effect |
|-------|---------|--------|
| `m` | 16 | Connections per node per layer. Higher = better recall, more RAM |
| `ef_construct` | 100 | Search width during build. Higher = better graph quality, slower ingest |
| `ef` (query) | 128 | Search width during query. Higher = better recall, more latency. Tunable at query time without rebuilding |
| `full_scan_threshold` | 10000 | Collections smaller than this use brute-force, not HNSW |

**Memory formula (approximate):**
```
RAM ≈ points × dims × 4 bytes (float32) + points × m × 2 × 8 bytes (graph edges)
```
For 100k points, 768 dims, m=16:
- Vectors: 100k × 768 × 4 = ~295 MB
- Graph: 100k × 16 × 2 × 8 = ~25 MB
- Total: ~320 MB

### Quantization — trading recall for memory

| Type | Compression | Recall loss | Use case |
|------|-------------|-------------|----------|
| None (float32) | 1x | 0% | Default, best quality |
| Scalar (int8) | 4x | <1-2% | Production default for cost/quality |
| Product (PQ) | 8–32x | 3-10% | Large-scale, memory-constrained |
| Binary | 32x | 5-15% | Extreme scale with oversampling+rerank |

Qdrant rescoring: after quantized ANN search, re-scores top results with
original float32 vectors. This largely recovers recall loss.

### Segments — Qdrant's storage engine

A segment is an immutable unit of storage (like an SSTable in LSM tree DBs).
Fresh inserts land in the "appendable" (mutable) segment. The optimizer:
1. Merges small segments to reduce query fan-out
2. Builds HNSW over merged segments
3. Applies quantization if configured

Watch `segments_count` after heavy ingest — many segments = optimizer busy.

---

## Labs

```bash
task w1:distance    # Compare cosine / euclid / dot product on real data
task w1:hnsw        # Vary m and ef_construct, benchmark recall vs latency
task w1:quant       # Apply scalar quantization, measure memory and recall
task w1:segments    # Inspect segment lifecycle during ingest
```

---

## Interview questions for this week

**"Why would HNSW recall degrade over time?"**
Three causes:
1. Heavy deletions fragment the graph — deleted nodes leave "holes" that
   disconnect graph paths. Qdrant marks deleted vectors as tombstones, not
   graph surgery. The optimizer rebuilds segments to clean this up, but under
   constant delete load the graph quality degrades between rebuilds.
2. Low `m` for dataset size — if m=8 and you have 1M points, the graph is
   too sparse to reliably reach nearest neighbors.
3. `ef` too low at query time — you're not traversing enough nodes to find
   the true top-K.

**"What breaks first at 10M points, 768 dims, on a 16GB node?"**
RAM. At float32: 10M × 768 × 4 = 30.7 GB. Does not fit. Solutions:
1. Use scalar quantization (int8): 30.7 GB → 7.7 GB, fits with overhead
2. Enable `on_disk=true` for HNSW graph (vectors stay on disk, index in RAM)
3. Add more nodes (distributed mode, shards)

**"When would you avoid HNSW entirely?"**
- Collection smaller than `full_scan_threshold` (10k points default) — brute
  force is faster because HNSW overhead dominates
- Exact recall required (compliance, dedup) — use `exact=True` in SearchParams
- Very high filter selectivity (<1% results) — filtered brute-force may beat
  filtered HNSW because the candidate set is too small for graph traversal
