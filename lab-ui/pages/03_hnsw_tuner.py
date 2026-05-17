"""HNSW Tuner — create test collections with different params, benchmark recall vs latency."""

import os
import sys
import time
from pathlib import Path
from statistics import mean

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.qdrant_helpers import get_client

st.set_page_config(page_title="HNSW Tuner", layout="wide")
st.title("HNSW Tuner")
st.caption("Week 1 — benchmark m / ef_construct / ef recall vs latency tradeoffs")

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
REPO_ROOT   = Path(__file__).resolve().parents[2]
DOCS_PATH   = REPO_ROOT / "docs"
sys.path.insert(0, str(REPO_ROOT / "ingest"))

try:
    from chunker import chunk_markdown, chunk_hcl
except ImportError:
    st.error("Cannot import chunker — make sure ingest/ is in the repo root.")
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Benchmark config")
    m_values   = st.multiselect("m values to test", [4, 8, 16, 32, 64], default=[8, 16, 32])
    ef_c_vals  = st.multiselect("ef_construct values", [50, 100, 200, 400], default=[100, 200])
    ef_runtime = st.multiselect("ef at query time", [32, 64, 128, 256], default=[64, 128])
    k          = st.slider("Recall@K", 1, 20, 10)
    max_files  = st.slider("Max doc files for corpus", 2, 15, 5)
    run_btn    = st.button("Run benchmark", type="primary")

st.markdown("""
### What this measures

1. Creates one collection per `(m, ef_construct)` combination
2. Ingests the same document corpus into each
3. Runs identical queries with different `ef` values at query time
4. Compares against **exact brute-force** (ground truth) to compute recall
5. Reports: ingest time, P50 latency, P99 latency, Recall@K

> **ef at query time** is free to change per-request without rebuilding.
> `m` and `ef_construct` are fixed at collection creation — changing them
> requires a full re-index.
""")

if not run_btn:
    st.info("Configure parameters in the sidebar and click **Run benchmark**.")
    st.stop()

if not m_values or not ef_c_vals:
    st.error("Select at least one m and one ef_construct value.")
    st.stop()

# ── Load corpus ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Embedding corpus ...")
def load_and_embed(max_files: int):
    import ollama
    ol = ollama.Client(host=OLLAMA_URL)
    corpus = []
    exts = {".md", ".hcl", ".tf"}
    files = [f for f in sorted(DOCS_PATH.rglob("*")) if f.suffix in exts][:max_files]
    for f in files:
        text   = f.read_text(errors="ignore")
        chunks = chunk_markdown(text) if f.suffix == ".md" else chunk_hcl(text)
        for c in chunks[:3]:
            vec = ol.embed(model=EMBED_MODEL, input=c).embeddings[0]
            corpus.append((vec, c[:80], str(f.name)))
    return corpus

try:
    corpus = load_and_embed(max_files)
except Exception as e:
    st.error(f"Embedding failed: {e}\nCheck Ollama at {OLLAMA_URL}")
    st.stop()

st.success(f"Corpus: {len(corpus)} chunks embedded")
query_vecs = [v for v, _, _ in corpus[:min(5, len(corpus))]]


def measure(qd, col, queries, k, ef_val):
    from qdrant_client.models import SearchParams
    lats, recs = [], []
    for qvec in queries:
        t0   = time.perf_counter()
        hnsw = qd.query_points(
            collection_name=col,
            query=qvec,
            limit=k,
            search_params=SearchParams(hnsw_ef=ef_val, exact=False),
        ).points
        lats.append((time.perf_counter() - t0) * 1000)

        exact = qd.query_points(
            collection_name=col,
            query=qvec,
            limit=k,
            search_params=SearchParams(exact=True),
        ).points
        recs.append(len({r.id for r in hnsw} & {r.id for r in exact}) / k)

    sl = sorted(lats)
    return {
        "p50_ms":  sl[len(sl)//2],
        "p99_ms":  sl[max(-1, int(len(sl)*0.99))],
        "recall":  mean(recs),
    }


# ── Run benchmark ─────────────────────────────────────────────────────────
from qdrant_client.models import Distance, HnswConfigDiff, PointStruct, VectorParams

qd = get_client()
rows = []

progress = st.progress(0.0)
status   = st.empty()
total    = len(m_values) * len(ef_c_vals) * len(ef_runtime)
done     = 0

for m in m_values:
    for ef_c in ef_c_vals:
        col = f"lab-hnsw-m{m}-ef{ef_c}"
        status.text(f"Building {col} ...")

        if qd.collection_exists(col):
            qd.delete_collection(col)
        qd.create_collection(
            collection_name=col,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            hnsw_config=HnswConfigDiff(m=m, ef_construct=ef_c),
        )
        points = [PointStruct(id=i, vector=v, payload={"t": t}) for i, (v, t, _) in enumerate(corpus)]
        t_ingest = time.perf_counter()
        qd.upsert(col, points)
        ingest_s = time.perf_counter() - t_ingest

        time.sleep(1)  # let optimizer start

        for ef_q in ef_runtime:
            metrics = measure(qd, col, query_vecs, k, ef_q)
            mem_mb  = len(corpus) * 768 * 4 / 1e6 + len(corpus) * m * 2 * 8 / 1e6
            rows.append({
                "m":         m,
                "ef_c":      ef_c,
                "ef_query":  ef_q,
                "ingest_s":  round(ingest_s, 2),
                "P50_ms":    round(metrics["p50_ms"], 1),
                "P99_ms":    round(metrics["p99_ms"], 1),
                f"Recall@{k}": round(metrics["recall"], 3),
                "Mem_MB":    round(mem_mb, 0),
            })
            done += 1
            progress.progress(done / total)

        qd.delete_collection(col)

status.text("Done. Collections cleaned up.")
progress.progress(1.0)

# ── Results table ─────────────────────────────────────────────────────────
df = pd.DataFrame(rows)
st.subheader("Benchmark results")
st.dataframe(
    df.style.background_gradient(subset=[f"Recall@{k}"], cmap="RdYlGn")
            .background_gradient(subset=["P50_ms"], cmap="RdYlGn_r"),
    use_container_width=True,
    hide_index=True,
)

best = df.loc[df[f"Recall@{k}"].idxmax()]
fast = df.loc[df["P50_ms"].idxmin()]

c1, c2 = st.columns(2)
c1.success(f"Best recall: m={best['m']}, ef_c={best['ef_c']}, ef_q={best['ef_query']} → Recall={best[f'Recall@{k}']:.1%}")
c2.info(f"Fastest:      m={fast['m']}, ef_c={fast['ef_c']}, ef_q={fast['ef_query']} → P50={fast['P50_ms']}ms")

with st.expander("How to read this table"):
    st.markdown(f"""
    - **m**: Connections per node. Higher = better graph connectivity = better recall.
      Memory cost: `points × m × 2 × 8 bytes` for the graph edges.
    - **ef_c (ef_construct)**: Search width during index build. Higher = better neighbor
      selection = higher recall ceiling. Set once at collection creation.
    - **ef_query**: Search width during ANN traversal. Higher = more nodes explored =
      better recall but more latency. **You can change this per-request at runtime.**
    - **Recall@{k}**: Fraction of exact top-{k} results found by HNSW. 0.95 = 95% of
      the "true" nearest neighbors were returned.
    - **Mem_MB**: Estimated vector + graph RAM (vectors: `n×768×4B`, graph: `n×m×2×8B`).

    **Rule of thumb for production:**
    - Start with Qdrant defaults (m=16, ef_c=100)
    - If recall < 0.9: increase ef_query first (free), then ef_c (requires reindex)
    - If memory is the bottleneck: apply scalar quantization (4x reduction, <2% recall loss)
    - m=32 is the sweet spot for high-quality RAG (2x memory, meaningful recall gain)
    """)
