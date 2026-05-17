# Week 4 — Multi-tenancy, Reranking, and Multiple Collections

**Goal:** Implement production-grade tenant isolation. Add a cross-encoder
reranker. Understand when to use one collection vs many.

**Time:** 4–5 days  
**Prerequisites:** Weeks 0–3 complete

---

## Multi-tenancy patterns — deep comparison

### Pattern A: Payload filter (one collection, many tenants)

```python
# Ingest: tag each point
payload={"text": chunk, "tenant": "vault-team", "doc_type": "runbook"}

# Query: filter by tenant
Filter(must=[FieldCondition(key="tenant", match=MatchValue(value="vault-team"))])
```

**Pros:** Simple ops, single HNSW index, tenants share graph quality  
**Cons:** HNSW graph sees all tenants (recall may be lower for small tenants),
payload index required (mandatory, not optional), cross-tenant data isolation
is app-level only (not storage-level)

### Pattern B: One collection per tenant

```python
qd.create_collection(f"docs-{tenant_id}", ...)
```

**Pros:** Complete isolation (storage, HNSW, metrics per tenant), can tune HNSW
per tenant, deleting a tenant = deleting one collection  
**Cons:** N collections to manage, N HNSW indexes in RAM, Qdrant Cloud free
tier has collection limits, alerting becomes more complex

### Pattern C: Named vectors per tenant (anti-pattern)

Don't do this. Named vectors are for multi-modal search (text + image), not
tenant isolation. The vector space is shared.

**Interview answer:** "For < 100 tenants with similar document volumes,
Pattern A with payload indexes is operationally simpler and cost-effective.
For enterprise multi-tenant SaaS with strict data isolation requirements,
Pattern B (per-tenant collections) with collection-per-namespace authorization
is the right choice."

---

## Reranking

Two-stage retrieval:
1. Stage 1 (ANN): retrieve top-K*4 candidates fast (HNSW)
2. Stage 2 (reranker): score each candidate pair (query, chunk) with a
   cross-encoder model → re-sort → return top-K

```
                 fast retrieval          re-score
query ──────► HNSW (top 20) ──────► cross-encoder ──────► top 5
              ~10ms                 ~100-500ms
```

Cross-encoder models:
- `cross-encoder/ms-marco-MiniLM-L-6-v2` — small, fast
- `BAAI/bge-reranker-base` — strong quality
- `Cohere Rerank API` — cloud, highest quality, pay-per-use

When to use reranking:
- When top-1 precision matters more than latency (legal, compliance queries)
- When query + document relationship is complex (multi-hop reasoning)
- When embedding model shows poor ranking on your eval set

---

## Labs

```bash
task w4:tenants      # Simulate two tenants, measure isolation and recall impact
task w4:reranking    # Add cross-encoder reranking, compare MRR@5 before/after
task w4:collections  # Pattern A vs B performance comparison
```

---

## Interview questions

**"How do you delete all data for a tenant in Pattern A?"**
You cannot do a bulk delete by filter efficiently in older Qdrant versions.
Modern approach: `qd.delete(collection, Filter(must=[tenant_filter]))`.
In Pattern B, you simply `delete_collection(f"docs-{tenant_id}")`.
This is a key argument for Pattern B in true multi-tenant SaaS.

**"How would you migrate tenants from Pattern A to Pattern B without downtime?"**
1. Create new per-tenant collection
2. Scroll all points for that tenant from the shared collection
3. Re-upsert into the new collection (keep original vectors if stored, or re-embed)
4. Validate recall on eval dataset
5. Update application routing to use per-tenant collection name
6. Delete the tenant's points from shared collection
Blue/green at the collection level — Qdrant's collection aliases help here.

**"What breaks first in Pattern A at 10M points, 1000 tenants?"**
The HNSW graph becomes less effective per-tenant. If each tenant has 10k points
out of 10M, the tenant's points are sparse in the graph. The filter-then-ANN
search must traverse a larger graph to find the tenant-specific nearest neighbors.
Recall for small tenants degrades before large tenants.
Solution: Set per-tenant `full_scan_threshold` behavior or switch to Pattern B
for tenants exceeding a size threshold.
