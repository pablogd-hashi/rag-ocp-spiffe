"""Week 2 — Lab 1: Embedding model comparison using fastembed.

Embeds the same corpus with two models (different dims), creates separate
collections, runs identical queries, compares result ranking.

Requires: pip install fastembed

Run:  python weeks/week-2/experiments/01_embedding_comparison.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, SearchParams, VectorParams

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PATH = REPO_ROOT / "docs"
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from chunker import chunk_markdown, chunk_hcl

K = 5

# Models to compare — fastembed downloads them automatically
MODELS = [
    {
        "name":       "BAAI/bge-small-en-v1.5",
        "col_suffix": "bge-small",
        "dim":        384,
    },
    {
        "name":       "nomic-ai/nomic-embed-text-v1.5",
        "col_suffix": "nomic",
        "dim":        768,
    },
]

TEST_QUERIES = [
    "How does Vault issue SPIFFE certificates?",
    "What ports does Consul Connect use?",
    "How do I configure PKI intermediate CA?",
    "What is the default TTL for leaf certificates?",
]


def load_chunks(max_files: int = 10) -> list[tuple[str, str]]:
    """Return (chunk_text, source_path) pairs."""
    result = []
    exts = {".md", ".hcl", ".tf"}
    for f in sorted(DOCS_PATH.rglob("*"))[:max_files]:
        if f.suffix not in exts:
            continue
        text = f.read_text(errors="ignore")
        chunks = chunk_markdown(text) if f.suffix == ".md" else chunk_hcl(text)
        for c in chunks[:5]:
            result.append((c, str(f.relative_to(DOCS_PATH))))
    return result


def embed_all(model: TextEmbedding, texts: list[str]) -> list[list[float]]:
    return [list(v) for v in model.embed(texts)]


def build_collection(qd: QdrantClient, col: str, dim: int,
                     vectors: list[list[float]], chunks: list[tuple[str, str]]) -> float:
    if qd.collection_exists(col):
        qd.delete_collection(col)
    qd.create_collection(
        collection_name=col,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    points = [
        PointStruct(id=i, vector=vectors[i], payload={"text": chunks[i][0], "source": chunks[i][1]})
        for i in range(len(chunks))
    ]
    t0 = time.perf_counter()
    qd.upsert(col, points)
    return time.perf_counter() - t0


def main() -> None:
    qd = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=60, check_compatibility=False)

    print("\nLoading corpus ...")
    chunks = load_chunks()
    texts  = [c for c, _ in chunks]
    print(f"  {len(chunks)} chunks from {len(set(s for _,s in chunks))} files")

    collections = {}
    for m in MODELS:
        print(f"\nEmbedding with {m['name']} (dim={m['dim']}) ...")
        model   = TextEmbedding(model_name=m["name"])
        t0      = time.perf_counter()
        vecs    = embed_all(model, texts)
        emb_t   = time.perf_counter() - t0
        print(f"  Embedding time: {emb_t:.2f}s  ({emb_t/len(texts)*1000:.1f}ms/chunk)")

        col = f"w2-embed-{m['col_suffix']}"
        ingest_t = build_collection(qd, col, m["dim"], vecs, chunks)
        print(f"  Ingest time: {ingest_t:.2f}s")
        collections[m["col_suffix"]] = (col, m["dim"], m["name"], model)

    print("\n" + "="*70)
    print("RETRIEVAL COMPARISON — same queries, different models")
    print("="*70)

    for q in TEST_QUERIES:
        print(f"\nQuery: {q!r}")
        all_results = {}
        for suffix, (col, dim, mname, model) in collections.items():
            qvec = list(model.embed([q]))[0]
            results = qd.query_points(
                collection_name=col,
                query=list(qvec),
                limit=K,
                with_payload=True,
                search_params=SearchParams(exact=False),
            ).points
            all_results[suffix] = results
            print(f"\n  [{mname}]")
            for r in results[:3]:
                print(f"    score={r.score:.4f}  {r.payload['source']:30s}  {r.payload['text'][:50]!r}")

        # Overlap analysis
        if len(all_results) == 2:
            keys = list(all_results.keys())
            ids0 = {r.id for r in all_results[keys[0]]}
            ids1 = {r.id for r in all_results[keys[1]]}
            overlap = len(ids0 & ids1)
            print(f"\n  Result overlap (top {K}): {overlap}/{K} shared chunks")
            if overlap < K * 0.7:
                print("  ⚠ Low overlap — models rank different chunks as relevant")
                print("    This could indicate semantic coverage differences")

    print(f"""
Key takeaways:
  1. nomic-embed-text (768d) vs bge-small (384d): higher dims ≠ always better.
     Check actual result ranking on YOUR data, not just BEIR benchmarks.

  2. Speed matters at scale:
     384d model: half the memory, ~2x faster embedding, ~2x faster ANN search
     768d model: richer representation but doubles infrastructure cost at scale

  3. For RAG on technical docs with lots of product-specific terms:
     - Models trained on code + docs corpora (nomic, bge) outperform generic ones
     - Fine-tuning on your domain corpus is the highest-leverage quality improvement

  4. Embedding model lock-in is real: once in production at scale, re-embedding
     requires a maintenance window (or blue/green collection swap).

  Interview answer to "How would you choose an embedding model?":
    1. Start with a standard (nomic, bge-base) on your corpus
    2. Build a retrieval eval dataset (50-100 Q&A pairs from your docs)
    3. Measure recall@5 and MRR@5 for each candidate model
    4. Factor in: latency budget, memory budget, multilingual needs
    5. Consider fine-tuning if off-the-shelf models show poor recall on domain terms
""")

    for m in MODELS:
        qd.delete_collection(f"w2-embed-{m['col_suffix']}")
    print("  Scratch collections deleted.\n")


if __name__ == "__main__":
    main()
