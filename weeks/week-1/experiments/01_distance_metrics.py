"""Week 1 — Lab 1: Distance metric comparison on real doc embeddings.

Creates three identical collections (cosine / euclid / dot) ingesting the
same docs via Qdrant Cloud inference, runs the same queries, and compares
result ordering.

Key question: for normalized text embeddings, does the distance metric
actually change the results? (Spoiler: often not, but the reasons matter.)

Run:  python weeks/week-1/experiments/01_distance_metrics.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, Document, PointStruct, VectorParams

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
DIM            = int(os.environ.get("EMBED_DIM", "384"))

REPO_ROOT  = Path(__file__).resolve().parents[3]
DOCS_PATH  = REPO_ROOT / "docs"
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from chunker import chunk_markdown

TEST_QUERIES = [
    "How does Vault PKI issue certificates?",
    "What is SPIFFE and how does it work?",
    "How does Consul Connect use mTLS?",
]

COLLECTIONS = {
    "w1-cosine":    Distance.COSINE,
    "w1-euclid":    Distance.EUCLID,
    "w1-dot":       Distance.DOT,
}


def load_corpus() -> list[tuple[str, str]]:
    """Return (chunk_text, filename) pairs from first 5 markdown files."""
    corpus = []
    md_files = list(DOCS_PATH.rglob("*.md"))[:5]
    for f in md_files:
        text   = f.read_text(errors="ignore")
        chunks = chunk_markdown(text)[:3]
        for chunk in chunks:
            corpus.append((chunk, f.name))
    return corpus


def ingest_to(qd: QdrantClient, col: str, dist: Distance, corpus: list[tuple[str, str]]) -> int:
    if qd.collection_exists(col):
        qd.delete_collection(col)
    qd.create_collection(
        collection_name=col,
        vectors_config=VectorParams(size=DIM, distance=dist),
    )
    points = [
        PointStruct(
            id=i,
            vector=Document(text=chunk, model=EMBED_MODEL),
            payload={"text": chunk, "source": fname},
        )
        for i, (chunk, fname) in enumerate(corpus)
    ]
    qd.upsert(col, points)
    return len(points)


def query_col(qd: QdrantClient, col: str, question: str):
    return qd.query_points(
        collection_name=col,
        query=Document(text=question, model=EMBED_MODEL),
        limit=3,
        with_payload=True,
    ).points


def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=30,
        check_compatibility=False,
    )

    corpus = load_corpus()
    print(f"\nIngesting {len(corpus)} chunks into 3 collections via Qdrant Cloud inference ...")
    for col, dist in COLLECTIONS.items():
        n = ingest_to(qd, col, dist, corpus)
        print(f"  {col}: {n} points")

    print("\n" + "="*65)
    print("RESULT COMPARISON ACROSS DISTANCE METRICS")
    print("="*65)

    for q in TEST_QUERIES:
        print(f"\nQuery: {q}")
        print("-"*65)
        for col in COLLECTIONS:
            results = query_col(qd, col, q)
            print(f"\n  [{col}]")
            for r in results:
                print(f"    score={r.score:+.4f}  {r.payload['source']:20s}  {r.payload['text'][:60]!r}")

    print("\n" + "="*65)
    print("ANALYSIS")
    print("="*65)
    print(f"""
  {EMBED_MODEL} outputs L2-normalized vectors (magnitude = 1.0).
  For unit-normalized vectors:

    Cosine similarity  = dot(A,B) / (|A|*|B|) = dot(A,B)  (since |A|=|B|=1)
    Dot product        = dot(A,B)
    Euclidean distance = sqrt(2 - 2*dot(A,B))

  All three are monotonic transforms of each other for unit vectors.
  Result ORDERING should be identical. Scores will differ:
    cosine:  0.0 to 1.0 (1.0 = identical)
    dot:     0.0 to 1.0 (same as cosine for unit vectors)
    euclid:  0.0 to sqrt(2) (0.0 = identical, inverted meaning)

  Practical implication:
    For text RAG: cosine is the conventional choice — it's self-documenting.
    For OpenAI ada-002 or text-embedding-3: dot product is recommended
    (Ada vectors are normalized but OpenAI's docs prefer dot).
    For un-normalized vectors (raw image features, audio): euclidean.

  Interview trap: "Is cosine always better for text?"
    No. Cosine ignores magnitude. If your embedding model injects magnitude
    information (e.g., longer documents have higher magnitude to signal
    informativeness), dot product will use that signal; cosine discards it.
  """)

    # Cleanup
    for col in COLLECTIONS:
        qd.delete_collection(col)
    print("  Scratch collections deleted.\n")


if __name__ == "__main__":
    main()
