"""Week 0 — Lab 3: Multi-tenancy via payload filtering.

Simulates two tenants (vault-team, ocp-team) in the same collection.
Shows how payload indexes make tenant isolation fast at scale.

Run:  python weeks/week-0/experiments/03_payload_filters.py
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")


def hr(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print("─"*60)


def count_by_tenant(qd: QdrantClient, collection: str) -> dict[str, int]:
    """Count points per tenant using scroll (no vector needed)."""
    tenants: dict[str, int] = {}
    offset = None
    while True:
        points, next_offset = qd.scroll(
            collection_name=collection,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            t = p.payload.get("tenant", "unknown")
            tenants[t] = tenants.get(t, 0) + 1
        if next_offset is None:
            break
        offset = next_offset
    return tenants


def main() -> None:
    qd = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=30, check_compatibility=False)

    if not qd.collection_exists(COLLECTION):
        print(f"  Collection '{COLLECTION}' not found. Run: task w0:migrate")
        return

    # ── 1. Show current tenant distribution ─────────────────────────────
    hr("Tenant distribution in current collection")
    dist = count_by_tenant(qd, COLLECTION)
    total = sum(dist.values())
    print(f"  Total points: {total:,}")
    for tenant, count in sorted(dist.items()):
        pct = 100 * count / total if total else 0
        print(f"  tenant={tenant:20s}  {count:>6,} points  ({pct:.1f}%)")

    # ── 2. Filtered search: tenant isolation ─────────────────────────────
    hr("Filtered search — query within a single tenant")
    # Get a sample vector to use as query (from a real point)
    pts, _ = qd.scroll(COLLECTION, limit=1, with_vectors=True)
    if not pts:
        print("  No points found.")
        return
    query_vector = pts[0].vector

    for tenant in list(dist.keys())[:2]:
        t0 = time.perf_counter()
        results = qd.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            query_filter=Filter(must=[
                FieldCondition(key="tenant", match=MatchValue(value=tenant))
            ]),
            limit=5,
            with_payload=True,
        ).points
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"\n  tenant={tenant}  →  {len(results)} results  ({elapsed:.1f}ms)")
        for r in results[:2]:
            print(f"    score={r.score:.4f}  doc_type={r.payload.get('doc_type','?')}  source={r.payload.get('source','?')[:50]}")

    # ── 3. Filter by doc_type ─────────────────────────────────────────────
    hr("Filter by doc_type (architecture, runbook, policy)")
    print("  Counting points per doc_type ...")
    doc_types: dict[str, int] = {}
    offset = None
    while True:
        points, next_offset = qd.scroll(
            collection_name=COLLECTION,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            dt = p.payload.get("doc_type", "unknown")
            doc_types[dt] = doc_types.get(dt, 0) + 1
        if next_offset is None:
            break
        offset = next_offset

    for dtype, count in sorted(doc_types.items()):
        print(f"  doc_type={dtype:20s}  {count:>5,} points")

    # ── 4. Combined filter: tenant AND doc_type ───────────────────────────
    hr("Combined filter: tenant + doc_type (the production pattern)")
    if list(dist.keys()):
        tenant = list(dist.keys())[0]
        dtype  = list(doc_types.keys())[0]
        results = qd.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            query_filter=Filter(must=[
                FieldCondition(key="tenant",   match=MatchValue(value=tenant)),
                FieldCondition(key="doc_type", match=MatchValue(value=dtype)),
            ]),
            limit=5,
            with_payload=True,
        ).points
        print(f"\n  tenant={tenant} AND doc_type={dtype}")
        print(f"  Results: {len(results)}")
        for r in results[:3]:
            print(f"    score={r.score:.4f}  {r.payload.get('source','?')[:60]}")

    # ── 5. Explain payload index impact ──────────────────────────────────
    hr("Why payload indexes matter — architectural explanation")
    print("""
  WITHOUT a payload index on 'tenant':
    Every query with a tenant filter must read EVERY point's payload to check
    if tenant matches. With 100k points across 10 tenants, every user query
    reads 100k payloads to find 10k relevant ones.

  WITH a keyword payload index on 'tenant':
    Qdrant builds an inverted index: tenant → [point_id, point_id, ...]
    At query time, it looks up the tenant key in O(1), gets the list of
    candidate IDs, and only runs HNSW search within that candidate set.
    This is called "pre-filtering" — candidates are restricted BEFORE the
    ANN graph is traversed.

  Pre-filtering vs post-filtering:
    Pre-filter  (Qdrant default): filter first → HNSW on subset
                faster for high-selectivity filters (tenant isolates to <20%)
    Post-filter: HNSW on all → filter results
                risky: if the filter removes most results, top_k may not be met

  Qdrant automatically switches between strategies based on selectivity.
  The optimizer decides. You control it via min_score and ef at query time.
  """)

    print()


if __name__ == "__main__":
    main()
