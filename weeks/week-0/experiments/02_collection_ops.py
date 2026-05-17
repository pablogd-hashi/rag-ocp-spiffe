"""Week 0 — Lab 2: Collection CRUD and configuration.

This script creates, inspects, modifies, and deletes a scratch collection
so you can observe what each parameter does to the API response.

Run:  python weeks/week-0/experiments/02_collection_ops.py
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    OptimizersConfigDiff,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
SCRATCH        = "week0-scratch"


def hr(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print("─"*60)


def print_collection_config(qd: QdrantClient, name: str) -> None:
    info = qd.get_collection(name)
    cfg  = info.config.params
    hnsw = info.config.hnsw_config
    print(f"  points      : {info.points_count}")
    print(f"  segments    : {info.segments_count}")
    print(f"  distance    : {cfg.vectors.distance}")
    print(f"  hnsw.m      : {hnsw.m}")
    print(f"  hnsw.ef_c   : {hnsw.ef_construct}")
    print(f"  full_scan_t : {hnsw.full_scan_threshold}")
    print(f"  on_disk     : {hnsw.on_disk}")


def main() -> None:
    qd = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=30, check_compatibility=False)

    # Clean up from previous runs
    if qd.collection_exists(SCRATCH):
        qd.delete_collection(SCRATCH)
        print(f"  Deleted existing '{SCRATCH}'")

    # ── 1. Create with default HNSW ─────────────────────────────────────
    hr("Create collection with DEFAULT HNSW config")
    qd.create_collection(
        collection_name=SCRATCH,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    print_collection_config(qd, SCRATCH)
    print("""
  Key defaults (Qdrant v1.x):
    m=16           — each node has up to 16 bi-directional connections
    ef_construct=100 — during index build, Qdrant explores 100 candidates
                       to find the best m neighbors for each new point
    full_scan_threshold=10000
                   — if fewer points than this, Qdrant skips HNSW entirely
                     and does a brute-force scan (faster for tiny corpora)
  """)

    # ── 2. Create with high-recall HNSW ─────────────────────────────────
    hr("Create collection with HIGH-RECALL HNSW (m=32, ef_construct=200)")
    qd.delete_collection(SCRATCH)
    qd.create_collection(
        collection_name=SCRATCH,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        hnsw_config=HnswConfigDiff(m=32, ef_construct=200),
    )
    print_collection_config(qd, SCRATCH)
    print("""
  m=32:  each node connects to 32 neighbors → denser graph → better recall
         at the cost of ~2x memory (m=32 vs m=16) and ~2x slower inserts

  ef_construct=200: during build, explores 200 candidates per point →
         better neighbor selection → higher recall at query time
         cost: 2x slower index build time
  """)

    # ── 3. Upsert points and watch segment growth ────────────────────────
    hr("Upsert 20 points — watch segments grow")
    points = [
        PointStruct(
            id=i,
            vector=[round(i * 0.1 % 1, 3)] * 4,
            payload={"label": f"doc-{i}", "category": "A" if i % 2 == 0 else "B"},
        )
        for i in range(20)
    ]
    qd.upsert(collection_name=SCRATCH, points=points)
    info = qd.get_collection(SCRATCH)
    print(f"  After upsert: {info.points_count} points, {info.segments_count} segments")
    print(f"  Indexed: {info.indexed_vectors_count} / {info.points_count}")
    print("""
  Segments explanation:
    Fresh upserts land in a mutable "appendable" segment (essentially a
    write buffer). The background optimizer merges small appendable segments
    into larger immutable segments and rebuilds the HNSW graph over them.
    This is asynchronous — indexed_vectors can lag behind points_count.
  """)

    # ── 4. Payload indexes ───────────────────────────────────────────────
    hr("Create payload indexes")
    qd.create_payload_index(
        collection_name=SCRATCH,
        field_name="category",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    print("  Created keyword index on 'category'")
    print("""
  Types of payload indexes:
    KEYWORD   → inverted index, exact string match, good for tenant/tag/type
    INTEGER   → sorted index, range queries (e.g., timestamp > X)
    FLOAT     → sorted index, numeric ranges
    TEXT      → full-text index (tokenized), fuzzy/keyword search within payload
    BOOL      → bitmap index
    GEO       → geospatial index for radius/box queries

  Without a payload index: every query that filters on 'category' scans
  ALL payload documents (O(N)). With the index: O(log N + results).
  Critical for multi-tenant setups where tenant isolation is a filter.
  """)

    # ── 5. Filtered query ────────────────────────────────────────────────
    hr("Filtered query: only category=A")
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    results = qd.query_points(
        collection_name=SCRATCH,
        query=[0.1, 0.1, 0.1, 0.1],
        query_filter=Filter(must=[FieldCondition(key="category", match=MatchValue(value="A"))]),
        limit=5,
        with_payload=True,
    ).points
    print(f"  Results (all should have category=A): {len(results)}")
    for r in results:
        print(f"    id={r.id:3d}  category={r.payload['category']}  score={r.score:.4f}")

    # ── 6. Distance metrics side-by-side ─────────────────────────────────
    hr("Distance metric comparison")
    print("""
  Cosine:      measures angle between vectors, ignores magnitude.
               Best for text embeddings where direction = semantics.
               Formula: 1 - dot(A,B) / (|A| * |B|)

  Dot Product: cosine WITHOUT length normalization. Use when you want
               vector magnitude to influence ranking (importance-weighted).
               Same as cosine if vectors are unit-normalized (they often are).

  Euclidean:   measures straight-line distance (L2 norm).
               Sensitive to magnitude. Use for image/audio embeddings where
               magnitude carries information.

  Rule of thumb for interview:
    text embeddings → Cosine
    image/dense features already normalized → DotProduct (= Cosine)
    image/audio NOT normalized, or positional features → Euclidean

  Qdrant note: if you choose DotProduct, Qdrant expects vectors to be
  normalized for best results. It will NOT normalize them for you.
  """)

    # ── Cleanup ──────────────────────────────────────────────────────────
    qd.delete_collection(SCRATCH)
    print(f"\n  Deleted scratch collection '{SCRATCH}'\n")


if __name__ == "__main__":
    main()
