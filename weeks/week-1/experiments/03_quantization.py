"""Week 1 — Lab 3: Quantization — memory vs recall tradeoff.

Applies scalar quantization (INT8) and binary quantization to scratch
collections and measures impact on:
  - Memory estimate (vector storage portion)
  - Query latency
  - Recall@10 vs float32 ground truth

Uses Qdrant Cloud inference for embedding — no local Ollama needed.

Run:  python weeks/week-1/experiments/03_quantization.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    BinaryQuantization,
    BinaryQuantizationConfig,
    Distance,
    Document,
    PointStruct,
    QuantizationSearchParams,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
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

K = 10


def load_corpus(max_files: int = 15) -> list[str]:
    corpus = []
    extensions = {".md", ".hcl", ".tf"}
    for f in list(DOCS_PATH.rglob("*"))[:max_files]:
        if f.suffix not in extensions:
            continue
        text   = f.read_text(errors="ignore")
        chunks = (chunk_markdown(text) if f.suffix == ".md" else chunk_hcl(text))[:4]
        corpus.extend(chunks)
    return corpus


def build(qd: QdrantClient, name: str, corpus: list[str], quantization=None) -> None:
    if qd.collection_exists(name):
        qd.delete_collection(name)
    qd.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
        quantization_config=quantization,
    )
    points = [
        PointStruct(
            id=i,
            vector=Document(text=chunk, model=EMBED_MODEL),
            payload={"t": chunk[:80]},
        )
        for i, chunk in enumerate(corpus)
    ]
    qd.upsert(name, points)


def recall_and_latency(qd: QdrantClient, name: str, query_texts: list[str], k: int, rescore: bool = True):
    lats, recs = [], []
    qp = QuantizationSearchParams(rescore=rescore) if rescore else None
    for qtext in query_texts:
        qdoc = Document(text=qtext, model=EMBED_MODEL)
        t0 = time.perf_counter()
        hnsw = qd.query_points(
            collection_name=name,
            query=qdoc,
            limit=k,
            search_params=SearchParams(exact=False, quantization=qp),
        ).points
        lats.append((time.perf_counter() - t0) * 1000)
        exact = qd.query_points(
            collection_name=name,
            query=qdoc,
            limit=k,
            search_params=SearchParams(exact=True),
        ).points
        recs.append(len({r.id for r in hnsw} & {r.id for r in exact}) / k)
    sorted_lats = sorted(lats)
    return mean(recs), sorted_lats[len(sorted_lats)//2], sorted_lats[-1]


def mem_estimate(n: int, dim: int, bytes_per_val: float) -> str:
    mb = n * dim * bytes_per_val / 1e6
    return f"{mb:.1f} MB"


def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=60,
        check_compatibility=False,
    )

    print("\nLoading corpus ...")
    corpus      = load_corpus()
    query_texts = corpus[:5]
    n           = len(corpus)
    print(f"  {n} chunks  (embedding via Qdrant Cloud inference)")

    configs = [
        ("float32",     None),
        ("scalar-int8", ScalarQuantization(
            scalar=ScalarQuantizationConfig(type=ScalarType.INT8, always_ram=True)
        )),
        ("binary",      BinaryQuantization(
            binary=BinaryQuantizationConfig(always_ram=True)
        )),
    ]

    print("\nBuilding collections ...")
    for name, quant in configs:
        col = f"w1-quant-{name}"
        build(qd, col, corpus, quant)
        print(f"  {col}")

    time.sleep(3)

    print(f"\n{'Config':15s}  {'VecMem':>9s}  {'Compression':>12s}  {'RecallK':>9s}  {'P50ms':>7s}  {'P99ms':>7s}  Rescore")
    print("-" * 85)

    for name, _ in configs:
        col = f"w1-quant-{name}"
        if name == "float32":
            bpv, comp = 4.0, "1x (none)"
        elif name == "scalar-int8":
            bpv, comp = 1.0, "4x"
        else:
            bpv, comp = 0.125, "32x"

        rec, p50, p99 = recall_and_latency(qd, col, query_texts, K, rescore=True)
        print(f"  {name:15s}  {mem_estimate(n,DIM,bpv):>9s}  {comp:>12s}  {rec:>8.1%}  {p50:>6.1f}ms  {p99:>6.1f}ms  yes")

    # Binary without rescore for comparison
    rec_nr, p50_nr, p99_nr = recall_and_latency(qd, "w1-quant-binary", query_texts, K, rescore=False)
    print(f"  {'binary-no-rescore':15s}  {mem_estimate(n,DIM,0.125):>9s}  {'32x':>12s}  {rec_nr:>8.1%}  {p50_nr:>6.1f}ms  {p99_nr:>6.1f}ms  no")

    print(f"""
Quantization internals:

  Scalar INT8:
    Each float32 (4 bytes) compressed to int8 (1 byte) using min-max scaling.
    Qdrant stores the scale + offset per vector to decompress on rescore.
    Rescore: ANN search on compressed vectors → take top oversampling_factor×K
             → re-rank those with original float32 vectors → return top K.
    This recovers most of the recall loss at low latency cost.

  Binary:
    Each float32 → 1 bit (positive=1, negative=0 after centering).
    32× compression. Hamming distance used for fast similarity.
    High recall loss without rescore. With rescore: acceptable for many cases.
    Best for: extreme scale (100M+ points), where even scalar doesn't fit RAM.

  When to choose each:
    float32  → < 1M points, quality critical (legal, compliance)
    scalar   → 1M–100M points, production default, best cost/quality ratio
    binary   → 100M+ points, with rescore enabled, latency budget allows it

  Qdrant Cloud pricing: you pay for RAM. Scalar quantization is often the
  single highest-leverage optimization to reduce cost without hurting quality.
""")

    for name, _ in configs:
        qd.delete_collection(f"w1-quant-{name}")
    print("  Scratch collections deleted.\n")


if __name__ == "__main__":
    main()
