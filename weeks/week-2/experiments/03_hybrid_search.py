"""Week 2 — Lab 3: Hybrid search — dense + sparse vectors with RRF fusion.

Creates a collection with both dense (nomic-embed-text) and sparse (BM25)
vectors. Compares dense-only vs hybrid results for keyword-heavy queries.

Requires: pip install fastembed

Run:  python weeks/week-2/experiments/03_hybrid_search.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    NamedSparseVector,
    NamedVector,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PATH = REPO_ROOT / "docs"
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from chunker import chunk_markdown, chunk_hcl

COLLECTION = "w2-hybrid"
DENSE_DIM  = 384  # bge-small for speed
K          = 5

# Keyword-heavy queries where BM25 should help
KEYWORD_QUERIES = [
    "VAULT_ADDR",
    "connect_inter PKI",
    "spiffe://dc1/ns",
    "ef_construct HNSW",
    "consul gossip key",
]

# Semantic queries where dense should dominate
SEMANTIC_QUERIES = [
    "How does certificate rotation work?",
    "What happens when the CA expires?",
    "Explain the trust hierarchy for service identity",
]


def load_chunks(max_files=8):
    chunks = []
    for f in sorted(DOCS_PATH.rglob("*"))[:max_files]:
        if f.suffix not in {".md", ".hcl", ".tf"}:
            continue
        text = f.read_text(errors="ignore")
        parts = chunk_markdown(text) if f.suffix == ".md" else chunk_hcl(text)
        for c in parts[:4]:
            chunks.append((c, str(f.relative_to(DOCS_PATH))))
    return chunks


def main() -> None:
    qd = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=60, check_compatibility=False)

    print("\nLoading models ...")
    dense_model  = TextEmbedding("BAAI/bge-small-en-v1.5")
    sparse_model = SparseTextEmbedding("Qdrant/bm25")

    print("Loading corpus ...")
    chunks = load_chunks()
    texts  = [c for c, _ in chunks]
    print(f"  {len(chunks)} chunks")

    print("Embedding dense ...")
    dense_vecs = [list(v) for v in dense_model.embed(texts)]

    print("Embedding sparse (BM25) ...")
    sparse_vecs = list(sparse_model.embed(texts))

    # ── Build hybrid collection ──────────────────────────────────────────
    if qd.collection_exists(COLLECTION):
        qd.delete_collection(COLLECTION)
    qd.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": VectorParams(size=DENSE_DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(),
        },
    )
    print(f"  Created hybrid collection '{COLLECTION}'")

    points = []
    for i, (chunk, source) in enumerate(chunks):
        sv = sparse_vecs[i]
        points.append(PointStruct(
            id=i,
            vector={
                "dense":  dense_vecs[i],
                "sparse": SparseVector(
                    indices=sv.indices.tolist(),
                    values=sv.values.tolist(),
                ),
            },
            payload={"text": chunk, "source": source},
        ))
    qd.upsert(COLLECTION, points)
    print(f"  Upserted {len(points)} points (dense + sparse)")

    def query_dense(q: str):
        qvec = list(dense_model.embed([q]))[0]
        return qd.query_points(
            collection_name=COLLECTION,
            query=qvec,
            using="dense",
            limit=K,
            with_payload=True,
        ).points

    def query_hybrid(q: str):
        qd_sparse = list(sparse_model.embed([q]))[0]
        qd_dense  = list(dense_model.embed([q]))[0]
        # RRF fusion via Qdrant's native prefetch + fusion
        return qd.query_points(
            collection_name=COLLECTION,
            prefetch=[
                Prefetch(query=list(qd_dense), using="dense", limit=K*3),
                Prefetch(
                    query=SparseVector(
                        indices=qd_sparse.indices.tolist(),
                        values=qd_sparse.values.tolist(),
                    ),
                    using="sparse",
                    limit=K*3,
                ),
            ],
            query=None,  # RRF fusion when no top-level query
            limit=K,
            with_payload=True,
        ).points

    def show_comparison(q: str, label: str) -> None:
        print(f"\n  Query [{label}]: {q!r}")
        dense_r  = query_dense(q)
        hybrid_r = query_hybrid(q)

        dense_ids  = [r.id for r in dense_r]
        hybrid_ids = [r.id for r in hybrid_r]
        promoted   = [i for i in hybrid_ids if i not in dense_ids[:K]]

        print(f"    Dense-only top-3:")
        for r in dense_r[:3]:
            print(f"      id={r.id:3d}  score={r.score:.4f}  {r.payload['text'][:60]!r}")
        print(f"    Hybrid (RRF) top-3:")
        for r in hybrid_r[:3]:
            promoted_flag = " ← promoted by sparse" if r.id in promoted else ""
            print(f"      id={r.id:3d}  {r.payload['text'][:60]!r}{promoted_flag}")

    print("\n" + "="*65)
    print("KEYWORD-HEAVY QUERIES (sparse should help)")
    print("="*65)
    for q in KEYWORD_QUERIES:
        show_comparison(q, "keyword")

    print("\n" + "="*65)
    print("SEMANTIC QUERIES (dense should dominate)")
    print("="*65)
    for q in SEMANTIC_QUERIES:
        show_comparison(q, "semantic")

    print(f"""

Hybrid search internals:
  1. Dense retrieval: embed query → cosine ANN in HNSW → top K*3 candidates
  2. Sparse retrieval: BM25 term match → inverted index lookup → top K*3 candidates
  3. RRF fusion: score = 1/(60+rank_dense) + 1/(60+rank_sparse)
     Higher combined rank = better position in final list
  4. Return top K from fused list

When to use hybrid:
  ✓ Queries containing config keys, IP addresses, version strings, CLI flags
  ✓ Proper nouns not in embedding training corpus
  ✓ Short queries (1-2 words) where semantic context is minimal
  ✗ Long natural-language questions (dense already captures semantics)
  ✗ When you need to minimize latency (two ANN searches instead of one)

Qdrant sparse vector support:
  - Stored separately from dense vectors
  - Uses IDF-weighted inverted index internally
  - SparseVectorParams(modifier=Modifier.IDF) gives BM25-like behavior
  - fastembed's Qdrant/bm25 model is a drop-in

Interview question: "How does hybrid search affect your infrastructure cost?"
  Two vector types = roughly 2x storage. The sparse component is typically
  smaller than dense for short docs (few non-zero terms). At 1M points with
  768-dim dense + avg 50 non-zero sparse terms:
    dense: 1M × 768 × 4B = 3.1 GB
    sparse: 1M × 50 × (4+4)B = 0.4 GB  (indices + values, int+float)
    total overhead: ~13% — worth it for keyword-heavy workloads
""")

    qd.delete_collection(COLLECTION)
    print("  Scratch collection deleted.\n")


if __name__ == "__main__":
    main()
