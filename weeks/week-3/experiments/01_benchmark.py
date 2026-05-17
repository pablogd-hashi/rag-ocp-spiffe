"""Week 3 — Lab 1: Throughput and latency benchmark under concurrent load.

Tests Qdrant Cloud query throughput at 1, 5, 10, and 20 concurrent clients.
Measures P50, P95, P99 latency and queries/sec at each concurrency level.
Uses Qdrant Cloud inference for embedding — no local Ollama needed.

Key learning: Qdrant handles concurrent reads well (HNSW is read-safe).
Write throughput degrades during segment merging — concurrent reads are fine.

Run:  python weeks/week-3/experiments/01_benchmark.py
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Document

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")
TOP_K          = 5

TEST_QUESTIONS = [
    "How does Vault PKI issue certificates?",
    "What is SPIFFE and how does it work?",
    "How does Consul Connect use mTLS?",
    "What namespaces run in the RAG platform?",
    "How are service accounts configured in OCP?",
    "What is the Qdrant collection configuration?",
    "How does the embedding model generate vectors?",
    "What Vault policies are required for the platform?",
    "How do I check Consul service health?",
    "What does the ingest pipeline do?",
]


def single_query(qd: QdrantClient, question: str) -> float:
    t0 = time.perf_counter()
    qd.query_points(
        collection_name=COLLECTION,
        query=Document(text=question, model=EMBED_MODEL),
        limit=TOP_K,
        with_payload=False,
    )
    return (time.perf_counter() - t0) * 1000


def run_concurrent(questions: list[str], concurrency: int, total: int) -> list[float]:
    import random
    qd    = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, cloud_inference=True, timeout=30, check_compatibility=False)
    tasks = [questions[i % len(questions)] for i in range(total)]
    random.shuffle(tasks)
    latencies: list[float] = []

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(single_query, qd, q) for q in tasks]
        for f in as_completed(futures):
            latencies.append(f.result())
    return latencies


def percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=30,
        check_compatibility=False,
    )

    info = qd.get_collection(COLLECTION)
    print(f"\nCollection: {COLLECTION}  ({info.points_count:,} points)")
    print(f"Embed model: {EMBED_MODEL} (Qdrant Cloud inference)")
    print(f"Running concurrency benchmark ...\n")

    total_queries = 50

    print(f"{'Concurrency':>12s}  {'Total':>6s}  {'QPS':>7s}  {'P50ms':>7s}  {'P95ms':>7s}  {'P99ms':>7s}  {'Maxms':>7s}")
    print("-" * 68)

    for c in [1, 5, 10, 20]:
        t_start = time.perf_counter()
        lats = run_concurrent(TEST_QUESTIONS, concurrency=c, total=total_queries)
        elapsed = time.perf_counter() - t_start
        qps = len(lats) / elapsed

        p50  = percentile(lats, 50)
        p95  = percentile(lats, 95)
        p99  = percentile(lats, 99)
        pmax = max(lats)

        print(f"  {c:>10d}  {total_queries:>6d}  {qps:>6.1f}/s  {p50:>6.1f}ms  {p95:>6.1f}ms  {p99:>6.1f}ms  {pmax:>6.1f}ms")

    print(f"""
Benchmark internals:

  HNSW is read-safe — concurrent queries share the same immutable graph.
  No locking on reads. Throughput scales nearly linearly up to ~20 threads
  on a single Qdrant node (it uses Rayon for parallel search).

  What WILL degrade under high concurrency:
  - The optimizer runs in the background merging segments.
    During heavy ingest + concurrent reads, CPU contention appears.
  - Network RTT dominates at low concurrency (Qdrant Cloud → your machine
    has ~20-100ms RTT depending on region). Run from the same region for
    accurate benchmarks.

  Qdrant UI metrics to watch (cloud.qdrant.io -> Monitoring):
    grpc_responses_total      (throughput, latency histogram)
    rest_responses_fail_total (error rate — alert if rising)
    segment_optimizer_progress (1 = optimizing, 0 = idle)

  → Qdrant UI: your-cluster -> Monitoring -> search_duration_seconds
""")


if __name__ == "__main__":
    main()
