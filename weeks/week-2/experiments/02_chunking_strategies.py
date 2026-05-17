"""Week 2 — Lab 2: Chunking strategy comparison with named vectors.

ONE collection, THREE named vector spaces (fixed / sentence / paragraph).
Each chunk lives in exactly one vector space — the one matching its strategy.
Search with `using="fixed"` to query only fixed-chunked points, etc.

This mirrors the Qdrant Day-1 course assignment, adapted to this domain
(platform engineering docs: Vault PKI, Consul Connect, SPIFFE, OCP).

Key architectural concept: named vectors let you run A/B tests inside a
single collection without managing multiple collections. You pay the
cost of N named vector configs in RAM but save the ops overhead.

Uses Qdrant Cloud inference — no local Ollama needed for embedding.

Run:  python weeks/week-2/experiments/02_chunking_strategies.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Document,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
DIM            = int(os.environ.get("EMBED_DIM", "384"))

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PATH = REPO_ROOT / "docs"

COLLECTION = "w2-chunking-strategies"

TEST_QUERIES = [
    "How does Vault issue TLS certificates for Consul?",
    "What is a SPIFFE ID and how is it formatted?",
    "How do service intentions enforce deny-by-default policy?",
    "What happens when the Consul leader fails during an election?",
    "How does Vault PKI rotate intermediate certificates?",
]


# ── Chunking strategies ───────────────────────────────────────────────────

def fixed_size_chunks(text: str, chunk_size: int = 300, overlap: int = 50) -> list[str]:
    """Split by character count with overlap.
    Fast, predictable chunk sizes. Ignores sentence/paragraph boundaries —
    may split mid-sentence. Good baseline; bad for technical prose.
    """
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start : start + chunk_size].strip()
        if len(chunk) > 40:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def sentence_chunks(text: str, max_sentences: int = 3) -> list[str]:
    """Group sentences into fixed-count windows.
    Preserves grammatical units. Chunk sizes vary with sentence length.
    Good for narrative prose; less predictable for lists and code blocks.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    chunks = []
    for i in range(0, len(sentences), max_sentences):
        chunk = " ".join(sentences[i : i + max_sentences])
        if chunk:
            chunks.append(chunk)
    return chunks


def paragraph_chunks(text: str) -> list[str]:
    """Split on blank lines (double newline).
    Preserves semantic paragraphs and Markdown sections. Chunk sizes vary
    widely. Best for structured docs where authors put one idea per paragraph.
    Technical runbooks are often well-served by this strategy.
    """
    raw = [p.strip() for p in text.split("\n\n") if p.strip()]
    # Merge very short paragraphs (headings alone) with the next one
    merged = []
    buf = ""
    for p in raw:
        if len(p) < 60 and not buf:
            buf = p
        elif buf:
            merged.append(buf + "\n\n" + p)
            buf = ""
        else:
            merged.append(p)
    if buf:
        merged.append(buf)
    return [c for c in merged if len(c) > 40]


STRATEGIES = {
    "fixed":     fixed_size_chunks,
    "sentence":  sentence_chunks,
    "paragraph": paragraph_chunks,
}


# ── Build the collection ──────────────────────────────────────────────────

def build_collection(qd: QdrantClient) -> int:
    if qd.collection_exists(COLLECTION):
        qd.delete_collection(COLLECTION)

    # ONE collection, THREE named vector spaces — one per chunking strategy.
    # Each named vector has the same dimension and distance metric.
    # Points will only populate the vector space matching their strategy.
    qd.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "fixed":     VectorParams(size=DIM, distance=Distance.COSINE),
            "sentence":  VectorParams(size=DIM, distance=Distance.COSINE),
            "paragraph": VectorParams(size=DIM, distance=Distance.COSINE),
        },
    )

    # Payload index on chunk_strategy — needed for analyze_chunking_effectiveness()
    qd.create_payload_index(
        collection_name=COLLECTION,
        field_name="chunk_strategy",
        field_schema=PayloadSchemaType.KEYWORD,
    )

    # Load corpus: first 8 markdown files from docs/
    md_files = sorted(DOCS_PATH.rglob("*.md"))[:8]
    if not md_files:
        sys.exit(f"No .md files found under {DOCS_PATH}")

    print(f"\nCorpus: {len(md_files)} files")
    for f in md_files:
        print(f"  {f.relative_to(REPO_ROOT)}")

    points: list[PointStruct] = []
    point_id = 0

    for fpath in md_files:
        text = fpath.read_text(errors="ignore")
        rel  = str(fpath.relative_to(DOCS_PATH))

        for strategy_name, chunk_fn in STRATEGIES.items():
            chunks = chunk_fn(text)
            for chunk_idx, chunk in enumerate(chunks):
                # Each point has exactly ONE named vector — its strategy's space.
                # Querying with using="fixed" only searches fixed-chunked points.
                points.append(PointStruct(
                    id=point_id,
                    vector={
                        strategy_name: Document(text=chunk, model=EMBED_MODEL)
                    },
                    payload={
                        "chunk":          chunk,
                        "source":         rel,
                        "chunk_strategy": strategy_name,
                        "chunk_index":    chunk_idx,
                        "chunk_len":      len(chunk),
                    },
                ))
                point_id += 1

    # upload_points handles batching automatically (equivalent to batched upsert)
    qd.upload_points(collection_name=COLLECTION, points=points)
    print(f"\nUploaded {len(points)} chunks across {len(STRATEGIES)} strategies")
    return len(points)


# ── Compare search results ────────────────────────────────────────────────

def compare_search_results(qd: QdrantClient, query: str, limit: int = 3) -> None:
    print(f"\nQuery: '{query}'\n")

    for strategy in STRATEGIES:
        # using= selects which named vector space to search in
        results = qd.query_points(
            collection_name=COLLECTION,
            query=Document(text=query, model=EMBED_MODEL),
            using=strategy,
            limit=limit,
            with_payload=True,
        ).points

        print(f"  --- {strategy.upper()} CHUNKING ---")
        for i, r in enumerate(results, 1):
            print(f"  {i}. score={r.score:.3f}  source={r.payload['source']}")
            print(f"     [{r.payload['chunk_len']} chars]  {r.payload['chunk'][:90].strip()} ...")
        print()


# ── Analyze chunk statistics ──────────────────────────────────────────────

def analyze_chunking_effectiveness(qd: QdrantClient) -> None:
    print("\nCHUNKING STRATEGY STATISTICS")
    print("=" * 50)

    for strategy in STRATEGIES:
        points, _ = qd.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(must=[
                FieldCondition(key="chunk_strategy", match=MatchValue(value=strategy))
            ]),
            limit=500,
            with_payload=True,
            with_vectors=False,
        )

        sizes = [p.payload["chunk_len"] for p in points]
        if not sizes:
            continue

        print(f"\n  {strategy.upper()} STRATEGY:")
        print(f"    Total chunks : {len(sizes)}")
        print(f"    Avg size     : {mean(sizes):.0f} chars")
        print(f"    Size range   : {min(sizes)}–{max(sizes)} chars")


# ── Score distribution ────────────────────────────────────────────────────

def score_distribution(qd: QdrantClient) -> None:
    print("\nSCORE DISTRIBUTION ACROSS STRATEGIES")
    print("(mean cosine score across all test queries, top-5 results each)")
    print("=" * 55)
    print(f"{'Strategy':>12}  {'Mean':>7}  {'Max':>7}  {'Min':>7}")
    print("-" * 40)

    for strategy in STRATEGIES:
        all_scores = []
        for q in TEST_QUERIES:
            results = qd.query_points(
                collection_name=COLLECTION,
                query=Document(text=q, model=EMBED_MODEL),
                using=strategy,
                limit=5,
                with_payload=False,
            ).points
            all_scores.extend(r.score for r in results)

        if all_scores:
            print(f"  {strategy:>10}  {mean(all_scores):>7.3f}  {max(all_scores):>7.3f}  {min(all_scores):>7.3f}")


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=60,
        check_compatibility=False,
    )

    print("\n" + "=" * 60)
    print("  Week 2 — Chunking Strategy Comparison")
    print("  Domain: Platform engineering (Vault, Consul, SPIFFE, OCP)")
    print("  Embed:  Qdrant Cloud inference — " + EMBED_MODEL)
    print("=" * 60)

    build_collection(qd)

    # Compare all three strategies on the same queries
    print("\n" + "=" * 60)
    print("  PER-QUERY COMPARISON")
    print("=" * 60)

    for query in TEST_QUERIES[:3]:  # first 3 to keep output manageable
        compare_search_results(qd, query)

    # Chunk statistics
    analyze_chunking_effectiveness(qd)

    # Score distribution
    score_distribution(qd)

    print(f"""

ANALYSIS — Platform Engineering Docs
======================================

  FIXED (300 chars, 50 overlap):
    Predictable size, worst semantic coherence for technical prose.
    Headings land mid-chunk. Code blocks bisected. Config keys split
    from their values. High score variance — lucky hits score high,
    but many results are semantically incomplete fragments.

  SENTENCE (3 sentences per chunk):
    Better than fixed because grammatical units are preserved.
    Technical docs have long sentences with embedded config values.
    Multi-sentence chunks often mix two unrelated topics (a "Note:"
    sentence followed by a command example).

  PARAGRAPH (blank-line split):
    Best for this domain. Each paragraph in a Vault runbook or
    architecture doc covers exactly one concept. The author's own
    structure becomes the chunk boundary. Scores are lower on average
    (more content per chunk = diluted vector) but the retrieved
    context is immediately usable by the LLM.

  WINNER for technical runbooks/architecture docs: PARAGRAPH

  Why paragraph beats sentence for structured technical prose:
    Authors write one-idea-per-paragraph deliberately. The Markdown
    heading-aware chunker in ingest/chunker.py goes even further —
    it splits on ## and ### headings first, then falls back to
    fixed-size only for sections that exceed the size threshold.
    That heading-aware approach is what platform-docs uses in production.

  Interview question: "How does chunking affect RAG quality?"
    Too large: semantic signal diluted, cosine scores drop, LLM
    receives noisy context and hedges its answers.
    Too small: high precision scores but context is incomplete,
    LLM says "the documents don't cover this topic" even when they do.
    Wrong boundary (mid-sentence fixed): LLM receives grammatically
    incomplete text, producing confusing or wrong answers.
    Right boundary (paragraph/heading-aware): LLM receives a complete
    unit of meaning, answers are grounded and specific.

  → Qdrant UI: Collection → w2-chunking-strategies → Points
    Compare chunk payloads across strategies for the same source file.
""")

    qd.delete_collection(COLLECTION)
    print("  Scratch collection deleted.\n")


if __name__ == "__main__":
    main()
