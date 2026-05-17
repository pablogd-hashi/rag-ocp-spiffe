"""Week 1 — Lab 2: HNSW parameter tuning — recall vs latency benchmark.

Creates collections with different m / ef_construct values, measures:
  - Ingest time
  - Query latency (P50, P99)
  - Recall@10 vs exact brute-force search

Uses Qdrant Cloud inference for embedding — no local Ollama needed.

This is the lab that teaches you to answer: "What would you tune first for
a latency-sensitive RAG workload?"

Run:  python weeks/week-1/experiments/02_hnsw_tuning.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from statistics import mean, median

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Document,
    HnswConfigDiff,
    PointStruct,
    SearchParams,
    VectorParams,
)

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
DIM            = int(os.environ.get("EMBED_DIM", "384"))

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PATH = REPO_ROOT / "docs"
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from chunker import chunk_markdown, chunk_hcl

K = 10  # recall@K

CONFIGS = [
    {"name": "m8-ef50",   "m": 8,  "ef_construct": 50},
    {"name": "m16-ef100", "m": 16, "ef_construct": 100},   # Qdrant default
    {"name": "m32-ef200", "m": 32, "ef_construct": 200},
    {"name": "m64-ef400", "m": 64, "ef_construct": 400},   # high-recall
]

EF_RUNTIME_VALUES = [32, 64, 128, 256]


def load_corpus() -> list[str]:
    """Return text chunks from first 20 files (limit for speed)."""
    corpus = []
    extensions = {".md", ".hcl", ".tf"}
    for f in list(DOCS_PATH.rglob("*"))[:20]:
        if f.suffix not in extensions:
            continue
        text   = f.read_text(errors="ignore")
        chunks = (chunk_markdown(text) if f.suffix == ".md" else chunk_hcl(text))[:3]
        corpus.extend(chunks)
    return corpus


def build_collection(qd: QdrantClient, name: str, m: int, ef_c: int,
                     corpus: list[str]) -> float:
    if qd.collection_exists(name):
        qd.delete_collection(name)
    qd.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
        hnsw_config=HnswConfigDiff(m=m, ef_construct=ef_c),
    )
    points = [
        PointStruct(
            id=i,
            vector=Document(text=chunk, model=EMBED_MODEL),
            payload={"text": chunk[:80]},
        )
        for i, chunk in enumerate(corpus)
    ]
    t0 = time.perf_counter()
    qd.upsert(collection_name=name, points=points)
    return time.perf_counter() - t0


def measure_recall_and_latency(
    qd: QdrantClient, name: str, query_texts: list[str], k: int
) -> dict:
    latencies = []
    recalls   = []

    for qtext in query_texts:
        qdoc = Document(text=qtext, model=EMBED_MODEL)

        # HNSW search
        t0 = time.perf_counter()
        hnsw = qd.query_points(
            collection_name=name,
            query=qdoc,
            limit=k,
            search_params=SearchParams(exact=False),
        ).points
        latencies.append((time.perf_counter() - t0) * 1000)

        # Exact brute-force (ground truth) — same Document = same embedding
        exact = qd.query_points(
            collection_name=name,
            query=qdoc,
            limit=k,
            search_params=SearchParams(exact=True),
        ).points

        recalls.append(len({r.id for r in hnsw} & {r.id for r in exact}) / k)

    sorted_lat = sorted(latencies)
    return {
        "p50_ms":   sorted_lat[len(sorted_lat)//2],
        "p99_ms":   sorted_lat[max(0, int(len(sorted_lat)*0.99) - 1)],
        "mean_ms":  mean(latencies),
        "recall_k": mean(recalls),
    }


def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=60,
        check_compatibility=False,
    )

    print("\nLoading corpus from docs/ ...")
    corpus = load_corpus()
    print(f"  {len(corpus)} chunks  (embedding via Qdrant Cloud inference)")

    # Use first 5 chunks as query texts
    query_texts = corpus[:5]

    print(f"\nBuilding {len(CONFIGS)} collections with different HNSW configs ...")
    ingest_times = {}
    for cfg in CONFIGS:
        t = build_collection(qd, f"w1-{cfg['name']}", cfg["m"], cfg["ef_construct"], corpus)
        ingest_times[cfg["name"]] = t
        print(f"  {cfg['name']:15s}  ingest={t:.2f}s")

    print("\n  Waiting 3s for HNSW indexing ...")
    time.sleep(3)

    print(f"\n{'Config':15s}  {'Ingest':>8s}  {'P50ms':>7s}  {'P99ms':>7s}  {'Recall@'+str(K):>10s}  Memory estimate")
    print("-" * 75)

    results = {}
    for cfg in CONFIGS:
        name = f"w1-{cfg['name']}"
        m    = cfg["m"]
        info = qd.get_collection(name)
        metrics = measure_recall_and_latency(qd, name, query_texts, K)

        n        = info.points_count
        vec_mb   = n * DIM * 4 / 1e6
        graph_mb = n * m * 2 * 8 / 1e6
        total_mb = vec_mb + graph_mb

        print(f"  {cfg['name']:15s}  "
              f"{ingest_times[cfg['name']]:>6.2f}s  "
              f"{metrics['p50_ms']:>6.1f}ms  "
              f"{metrics['p99_ms']:>6.1f}ms  "
              f"{metrics['recall_k']:>9.1%}  "
              f"~{total_mb:.0f}MB")
        results[cfg["name"]] = metrics

    print(f"""
Analysis:
  m=8  ef=50  : lowest memory, fastest ingest, lowest recall — prototype only
  m=16 ef=100 : Qdrant default — good balance for most RAG workloads
  m=32 ef=200 : ~2x memory, ~50% recall improvement for high-precision needs
  m=64 ef=400 : diminishing returns unless dataset > 1M points and recall SLA > 99%

  Rule: increase m first (better graph structure), then ef_construct (better
  neighbor selection during build). ef at query time is cheapest to tune —
  you can change it per-request without rebuilding the index.
""")

    # ef runtime tuning on best config
    best_cfg = CONFIGS[-1]
    best_col = f"w1-{best_cfg['name']}"
    print(f"Runtime ef tuning on {best_cfg['name']} (m={best_cfg['m']}):")
    print(f"{'ef':>6}  {'P50ms':>7}  {'Recall@'+str(K):>10}")
    print("-" * 30)
    for ef in EF_RUNTIME_VALUES:
        lats, recs = [], []
        for qtext in query_texts:
            qdoc = Document(text=qtext, model=EMBED_MODEL)
            t0 = time.perf_counter()
            hnsw = qd.query_points(
                collection_name=best_col,
                query=qdoc,
                limit=K,
                search_params=SearchParams(hnsw_ef=ef, exact=False),
            ).points
            lats.append((time.perf_counter() - t0) * 1000)
            exact = qd.query_points(
                collection_name=best_col,
                query=qdoc,
                limit=K,
                search_params=SearchParams(exact=True),
            ).points
            recs.append(len({r.id for r in hnsw} & {r.id for r in exact}) / K)
        print(f"  {ef:>4}  {median(lats):>6.1f}ms  {mean(recs):>9.1%}")

    print("""
  ef at query time: you can dial recall vs latency per request.
  High-stakes queries (compliance, legal): ef=256+
  Interactive chat (latency sensitive):    ef=64
""")

    # Cleanup
    for cfg in CONFIGS:
        qd.delete_collection(f"w1-{cfg['name']}")
    print("  Scratch collections deleted.\n")


if __name__ == "__main__":
    main()
