"""Week 4 — Lab 2: Cross-encoder reranking — two-stage retrieval.

Stage 1 (fast): HNSW retrieves top-K*4 candidates via Qdrant Cloud inference
Stage 2 (precise): a cross-encoder scores each (query, chunk) pair → re-sort

Uses Qdrant Cloud inference for embedding — no local Ollama needed.

Requires: pip install sentence-transformers  (or: task setup:ml)

Run:  python weeks/week-4/experiments/02_reranking.py
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Document

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
TOP_K        = 5
CANDIDATE_K  = TOP_K * 4

TEST_QUESTIONS = [
    "How does Vault issue TLS certificates for Consul?",
    "What is SPIFFE and how does it relate to mTLS?",
    "How do I configure the Qdrant collection for multi-tenancy?",
]


def load_reranker():
    try:
        from sentence_transformers import CrossEncoder
        print(f"  Loading cross-encoder: {RERANK_MODEL} ...")
        return CrossEncoder(RERANK_MODEL)
    except ImportError:
        print("\n  ⚠  sentence-transformers not installed.")
        print("     Run: task setup:ml  (or: uv pip install sentence-transformers)")
        raise


def retrieve(qd: QdrantClient, question: str, k: int) -> list:
    return qd.query_points(
        collection_name=COLLECTION,
        query=Document(text=question, model=EMBED_MODEL),
        limit=k,
        with_payload=True,
    ).points


def rerank(cross_encoder, question: str, candidates: list) -> list:
    pairs  = [(question, r.payload.get("text", "")) for r in candidates]
    scores = cross_encoder.predict(pairs)
    ranked = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
    return [r for _, r in ranked]


def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=30,
        check_compatibility=False,
    )

    cross_enc = load_reranker()

    info = qd.get_collection(COLLECTION)
    print(f"\nCollection: {COLLECTION}  ({info.points_count:,} points)")
    print(f"Stage 1: HNSW top-{CANDIDATE_K} candidates via Qdrant Cloud inference")
    print(f"Stage 2: {RERANK_MODEL} → top-{TOP_K}\n")

    for question in TEST_QUESTIONS:
        print(f"{'─'*60}")
        print(f"Q: {question}")

        t0 = time.perf_counter()
        candidates = retrieve(qd, question, CANDIDATE_K)
        t_retrieve = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        reranked = rerank(cross_enc, question, candidates)[:TOP_K]
        t_rerank = (time.perf_counter() - t0) * 1000

        print(f"\n  Dense-only results (top {TOP_K} of {CANDIDATE_K} by cosine):  [{t_retrieve:.0f}ms]")
        for i, r in enumerate(candidates[:TOP_K], 1):
            print(f"    {i}. score={r.score:.3f}  {r.payload.get('source','?')}")
            print(f"       {r.payload.get('text','')[:80]} ...")

        print(f"\n  After reranking with {RERANK_MODEL}:  [{t_rerank:.0f}ms]")
        for i, r in enumerate(reranked, 1):
            print(f"    {i}. {r.payload.get('source','?')}")
            print(f"       {r.payload.get('text','')[:80]} ...")

        dense_ids = [r.id for r in candidates[:TOP_K]]
        moved = []
        for i, r in enumerate(reranked, 1):
            orig = dense_ids.index(r.id) + 1 if r.id in dense_ids else None
            if orig and orig != i:
                moved.append(f"rank {orig}→{i}: {r.payload.get('source','?')}")
        if moved:
            print(f"\n  Rank changes: {', '.join(moved)}")
        else:
            print(f"\n  No rank changes (dense ranking already optimal for this query)")

    print(f"""
Reranking internals:

  Bi-encoder (embedding model — Qdrant Cloud inference):
    - Encodes query and document SEPARATELY into fixed vectors
    - Similarity = cosine(query_vec, doc_vec)
    - Fast: O(dim) dot product per candidate
    - Limitation: query and document never "see" each other during encoding

  Cross-encoder (reranker — runs locally):
    - Feeds [CLS] query [SEP] document [SEP] through a full transformer
    - Output: a relevance score (not a vector)
    - Slow: full attention between query + document tokens
    - Accurate: captures query-document interaction (negation, specificity)

  Two-stage pattern:
    1. Dense retrieval (HNSW): top-20 in ~50ms (cloud inference)
    2. Cross-encoder rerank: top-5 from those 20 in ~100-500ms (local CPU)
    Net: better top-1 precision than dense alone

  When to use:
    - Legal/compliance: wrong answer has real cost
    - Complex multi-hop questions
    - When MRR@1 matters more than throughput

  → Qdrant UI: No reranking UI — it happens in your application layer.
""")


if __name__ == "__main__":
    main()
