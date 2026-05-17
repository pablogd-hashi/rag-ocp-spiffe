"""Week 3 — Lab 2: Ground truth retrieval evaluation with MRR@K.

Measures Mean Reciprocal Rank (MRR@K) on a hand-crafted eval dataset.
Runs the same questions at different hnsw_ef values to show the recall
vs latency tradeoff empirically.

Uses Qdrant Cloud inference for embedding — no local Ollama needed.

MRR@K = mean(1/rank_of_first_relevant_result) for each query
  - MRR@5 = 1.0  → correct doc always ranks #1
  - MRR@5 = 0.5  → correct doc at rank #2 on average
  - MRR@5 = 0.2  → correct doc at rank #5 on average

Run:  python weeks/week-3/experiments/02_retrieval_eval.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Document, SearchParams

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_FILE = Path(__file__).parent / "eval_dataset.json"

EVAL_DATASET = [
    {
        "question":          "How does Vault PKI issue TLS certificates?",
        "expected_keywords": ["vault", "pki", "certificate"],
        "expected_source":   "vault",
    },
    {
        "question":          "What is SPIFFE and what format is a SPIFFE ID?",
        "expected_keywords": ["spiffe", "svid", "trust domain"],
        "expected_source":   "spiffe",
    },
    {
        "question":          "How does Consul Connect establish mTLS between services?",
        "expected_keywords": ["consul", "connect", "mtls", "envoy"],
        "expected_source":   "consul",
    },
    {
        "question":          "What namespace does the RAG platform deploy into?",
        "expected_keywords": ["rag-platform", "namespace"],
        "expected_source":   "architecture",
    },
    {
        "question":          "How does the embedding model generate vector embeddings?",
        "expected_keywords": ["embed", "vector", "model"],
        "expected_source":   "architecture",
    },
]


def is_relevant(point_payload: dict, item: dict) -> bool:
    text   = (point_payload.get("text", "") or "").lower()
    source = (point_payload.get("source", "") or "").lower()
    keywords_hit = sum(1 for kw in item["expected_keywords"] if kw.lower() in text)
    source_hit   = item["expected_source"].lower() in source
    return keywords_hit >= 1 or source_hit


def mrr_at_k(results: list, item: dict, k: int) -> float:
    for rank, r in enumerate(results[:k], start=1):
        if is_relevant(r.payload, item):
            return 1.0 / rank
    return 0.0


def evaluate(qd: QdrantClient, hnsw_ef: int | None, k: int = 5) -> tuple[float, float]:
    sp = SearchParams(hnsw_ef=hnsw_ef) if hnsw_ef else None
    mrrs, lats = [], []

    for item in EVAL_DATASET:
        t0 = time.perf_counter()
        results = qd.query_points(
            collection_name=COLLECTION,
            query=Document(text=item["question"], model=EMBED_MODEL),
            limit=k,
            with_payload=True,
            search_params=sp,
        ).points
        lats.append((time.perf_counter() - t0) * 1000)
        mrrs.append(mrr_at_k(results, item, k))

    return mean(mrrs), mean(lats)


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
    print(f"Eval dataset: {len(EVAL_DATASET)} questions")
    print(f"Metric: MRR@5 — Mean Reciprocal Rank at K=5\n")

    ef_configs = [
        ("default (ef=collection)", None),
        ("ef=16  (fast, less accurate)", 16),
        ("ef=64  (balanced)", 64),
        ("ef=128 (accurate)", 128),
        ("ef=512 (high accuracy)", 512),
    ]

    print(f"{'Config':35s}  {'MRR@5':>7s}  {'Avg Latency':>12s}")
    print("-" * 60)

    for label, ef in ef_configs:
        mrr, lat = evaluate(qd, ef)
        print(f"  {label:33s}  {mrr:>6.3f}  {lat:>10.1f}ms")

    print(f"""
How to read MRR@5:

  1.000 → Correct chunk always appears at rank #1. Perfect.
  0.500 → Correct chunk at rank #2 on average.
  0.333 → Correct chunk at rank #3 on average.
  0.000 → Correct chunk never in top 5.

  Effect of hnsw_ef:
  - Low ef (16–32) cuts latency 2–5x but may miss correct results in a
    large graph. The ANN search has a narrower beam.
  - High ef (128–512) approaches exact search quality. Use for quality-critical
    queries (legal, compliance) where you can afford extra ms.
  - ef is tunable at QUERY TIME — no reindex needed.

  → Qdrant UI: Collection → Points → filter by source to spot-check chunks
""")

    if not EVAL_FILE.exists():
        EVAL_FILE.write_text(json.dumps(EVAL_DATASET, indent=2))
        print(f"  Eval dataset saved to: {EVAL_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
