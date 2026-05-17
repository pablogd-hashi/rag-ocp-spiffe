# Week 3 — Observability, Benchmarking, and Retrieval Evaluation

**Goal:** Instrument the RAG stack so retrieval quality and system health are
observable. Establish a ground truth eval dataset and baseline metrics.

**Time:** 4–5 days  
**Prerequisites:** Weeks 0–2 complete

---

## Qdrant metrics that matter

Qdrant exposes Prometheus metrics at `/metrics` on port 6333:

| Metric | What it signals |
|--------|----------------|
| `app_info` | Version and cluster node count |
| `collections_total` | Number of collections |
| `grpc_responses_total` | Query throughput and error rate |
| `rest_responses_fail_total` | HTTP error rate — alert on this |
| `segment_optimizer_progress` | 0=idle, 1=optimizing — spikes during heavy ingest |
| `qdrant_search_duration_seconds` | P50/P99 search latency per collection |
| `qdrant_collection_vectors_count` | Point count per collection |

**Critical alert: `rest_responses_fail_total` rising → check OOM or disk**

---

## Retrieval metrics that matter

These are NOT exposed by Qdrant — you build them in your application:

| Metric | Description | Good threshold |
|--------|-------------|----------------|
| `retrieval_top_score` | Cosine score of top result | > 0.6 for good corpus coverage |
| `retrieval_time_ms` | End-to-end retrieval latency | < 200ms for interactive |
| `chunks_returned` | Actual results vs top_k requested | < top_k → filter too restrictive |
| `embed_time_ms` | Question embedding time | < 100ms for interactive |

---

## Labs

```bash
task w3:bench    # Throughput and latency benchmark
task w3:eval     # Ground truth retrieval evaluation with MRR@K
```

### Lab 3.1 — Throughput benchmark
Concurrent query load test: 1, 5, 10, 20 concurrent clients.
Measure: P50, P95, P99 latency and queries/sec at each concurrency level.

Expected finding: Qdrant handles concurrent reads well (HNSW is read-safe).
Write throughput degrades under concurrent reads during segment merging.

### Lab 3.2 — Build your ground truth eval set

Create `weeks/week-3/eval_dataset.json`:
```json
[
  {
    "question": "What is the SPIFFE ID format?",
    "expected_source": "architecture/consul-connect-spiffe.md",
    "expected_keywords": ["spiffe://dc1", "ns/rag-platform"]
  },
  ...
]
```

Run this against your collection at different ef values and measure MRR@5.
This gives you a repeatable quality score — your benchmark baseline.

---

## Interview questions

**"How would you debug poor retrieval quality in production?"**
1. Check `retrieval_top_score` distribution — is it bimodal (some queries <0.4)?
2. For low-score queries: inspect what was retrieved — is it semantically wrong or just low scored?
3. Run the same query with `exact=True` — if exact search also returns junk, the problem is chunking or embedding, not HNSW
4. Check `indexed_vectors_count` vs `points_count` — if gap is large, optimizer is behind
5. Filter by doc_type/tenant — are you pulling from the wrong corpus?

**"What metrics would indicate bad chunking?"**
- High cosine scores (>0.8) but wrong answers from the LLM → chunks semantically similar but informationally wrong (too large, too noisy)
- Low cosine scores (<0.5) on obvious questions → chunks too large (diluted) or mid-sentence splits
- Many duplicate sources in results → overlap too aggressive
