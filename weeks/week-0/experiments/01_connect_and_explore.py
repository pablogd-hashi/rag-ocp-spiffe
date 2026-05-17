"""Week 0 — Lab 1: Connect to Qdrant Cloud and explore what's there.

Run:  python weeks/week-0/experiments/01_connect_and_explore.py
Prereq: .env loaded  (export $(cat .env | xargs))
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import SearchParams

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")


def hr(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print("─"*60)


def main() -> None:
    qd = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=10, check_compatibility=False)

    # ── 1. List all collections ──────────────────────────────────────────
    hr("Collections in your cluster")
    cols = qd.get_collections().collections
    if not cols:
        print("  No collections yet. Run task w0:migrate first.")
        sys.exit(0)
    broken = []
    for c in cols:
        try:
            info = qd.get_collection(c.name)
            print(f"  {c.name:30s}  {info.points_count:>8,} points")
        except Exception as e:
            print(f"  {c.name:30s}  ⚠ BROKEN ({e.__class__.__name__}) — run task w0:recover")
            broken.append(c.name)
    if broken:
        print(f"\n  Broken collections: {broken}")
        print("  Run: task w0:recover  then  task w0:migrate")
        sys.exit(1)

    # ── 2. Deep dive on target collection ───────────────────────────────
    if COLLECTION not in [c.name for c in cols]:
        print(f"\n  Collection '{COLLECTION}' not found — run task w0:migrate first.")
        sys.exit(0)

    hr(f"Collection detail: {COLLECTION}")
    info = qd.get_collection(COLLECTION)

    print(f"  Points total        : {info.points_count:,}")
    print(f"  Indexed vectors     : {info.indexed_vectors_count:,}")
    print(f"    (gap = points not yet in HNSW graph — optimizer is running)")
    print(f"  Segments            : {info.segments_count}")
    print(f"    (more segments = recent ingest; optimizer will merge them)")
    print(f"  Optimizer status    : {info.optimizer_status}")

    cfg  = info.config.params
    hnsw = info.config.hnsw_config
    vec  = cfg.vectors

    print(f"\n  Vector config")
    print(f"    size              : {vec.size}")
    print(f"    distance          : {vec.distance}")

    print(f"\n  HNSW config")
    print(f"    m                 : {hnsw.m}  (connections/node; default 16)")
    print(f"    ef_construct      : {hnsw.ef_construct}  (index-time search width; default 100)")
    print(f"    full_scan_threshold: {hnsw.full_scan_threshold}")
    print(f"      → collections with fewer points than this use brute-force, not HNSW")
    print(f"    on_disk           : {hnsw.on_disk}")

    print(f"\n  Payload schema (indexed fields)")
    if info.payload_schema:
        for field, schema in info.payload_schema.items():
            print(f"    {field:20s}: {schema.data_type}")
    else:
        print("    (no payload indexes — add them with create_payload_index)")

    # ── 3. Fetch a random point ──────────────────────────────────────────
    hr("Random point sample (scroll, no query)")
    points, _ = qd.scroll(
        collection_name=COLLECTION,
        limit=3,
        with_payload=True,
        with_vectors=False,
    )
    for p in points:
        print(f"\n  id      : {p.id}")
        for k, v in p.payload.items():
            val = (v[:120] + "...") if isinstance(v, str) and len(v) > 120 else v
            print(f"  {k:10s}: {val}")

    # ── 4. Run a test query and inspect results ──────────────────────────
    # We need a vector to query — grab one from an existing point
    hr("Test query (using a stored vector as query — tests ANN round-trip)")
    pts_with_vec, _ = qd.scroll(
        collection_name=COLLECTION,
        limit=1,
        with_payload=True,
        with_vectors=True,
    )
    if pts_with_vec:
        seed_point = pts_with_vec[0]
        results = qd.query_points(
            collection_name=COLLECTION,
            query=seed_point.vector,
            limit=3,
            with_payload=True,
        ).points
        print(f"  Query: re-query point {seed_point.id} against itself")
        print(f"  Source: {seed_point.payload.get('source', '?')}")
        print()
        for r in results:
            print(f"  score={r.score:.4f}  id={r.id}  source={r.payload.get('source','?')}")
        if results[0].id == seed_point.id:
            print("\n  ✓ Top result is the query point itself (score ≈ 1.0) — HNSW is working")
        else:
            print("\n  ⚠ Top result is NOT the query point — check if indexing is complete")

    print()


if __name__ == "__main__":
    main()
