"""Embedding Compare — same query, different models, compare ranking."""

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.qdrant_helpers import collection_names, get_client

st.set_page_config(page_title="Embedding Compare", layout="wide")
st.title("Embedding Compare")
st.caption("Week 2 — same query across different embedding models to compare ranking")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
REPO_ROOT  = Path(__file__).resolve().parents[2]
DOCS_PATH  = REPO_ROOT / "docs"
sys.path.insert(0, str(REPO_ROOT / "ingest"))

try:
    from chunker import chunk_markdown, chunk_hcl
except ImportError:
    st.error("Cannot import chunker.")
    st.stop()

with st.sidebar:
    st.header("Models to compare")
    use_nomic = st.checkbox("nomic-embed-text (768d, Ollama)", value=True)
    use_fastembed_small = st.checkbox("bge-small-en-v1.5 (384d, fastembed)", value=True)
    use_fastembed_nomic = st.checkbox("nomic-embed-text-v1.5 (768d, fastembed)", value=False)
    top_k = st.slider("top_k", 1, 10, 5)
    max_files = st.slider("Max files for temp collection", 2, 10, 4)

st.markdown("""
This page builds **temporary collections** — one per selected embedding model —
ingests the same documents, then runs your query against all of them.

> **Important:** Each model has its own vector space. A vector from model A cannot
> be used to query a collection built with model B. This is why you cannot
> change embedding models in production without re-indexing.
""")

question = st.text_input("Query", placeholder="How does Vault manage certificate rotation?")
run_btn  = st.button("Run comparison", type="primary")

if not run_btn or not question:
    st.info("Enter a query and click **Run comparison**.")
    st.stop()

models_to_run = []
if use_nomic:
    models_to_run.append({"label": "nomic-embed-text (Ollama)", "type": "ollama", "model": "nomic-embed-text", "dim": 768})
if use_fastembed_small:
    models_to_run.append({"label": "bge-small-en-v1.5 (fastembed)", "type": "fastembed", "model": "BAAI/bge-small-en-v1.5", "dim": 384})
if use_fastembed_nomic:
    models_to_run.append({"label": "nomic-text-v1.5 (fastembed)", "type": "fastembed", "model": "nomic-ai/nomic-embed-text-v1.5", "dim": 768})

if not models_to_run:
    st.warning("Select at least one model.")
    st.stop()

# ── Load chunks ────────────────────────────────────────────────────────────
@st.cache_data
def load_chunks(max_files):
    chunks = []
    for f in sorted(DOCS_PATH.rglob("*"))[:max_files]:
        if f.suffix not in {".md", ".hcl", ".tf"}:
            continue
        text = f.read_text(errors="ignore")
        parts = chunk_markdown(text) if f.suffix == ".md" else chunk_hcl(text)
        for c in parts[:4]:
            chunks.append((c, str(f.name)))
    return chunks

chunks = load_chunks(max_files)
texts  = [c for c, _ in chunks]
st.caption(f"Corpus: {len(chunks)} chunks from {max_files} files")


def embed_ollama(texts, model):
    import ollama
    ol = ollama.Client(host=OLLAMA_URL)
    return [ol.embed(model=model, input=t).embeddings[0] for t in texts]


def embed_fastembed(texts, model):
    from fastembed import TextEmbedding
    m = TextEmbedding(model_name=model)
    return [list(v) for v in m.embed(texts)]


qd = get_client()
from qdrant_client.models import Distance, PointStruct, VectorParams

all_results = {}
for m in models_to_run:
    col = f"lab-emb-{m['label'].replace(' ','_')[:20]}"
    with st.spinner(f"Embedding + indexing with {m['label']} ..."):
        if m["type"] == "ollama":
            vecs = embed_ollama(texts, m["model"])
            qvec = embed_ollama([question], m["model"])[0]
        else:
            vecs = embed_fastembed(texts, m["model"])
            from fastembed import TextEmbedding
            qvec = list(TextEmbedding(m["model"]).embed([question]))[0].tolist()

        if qd.collection_exists(col):
            qd.delete_collection(col)
        qd.create_collection(col, vectors_config=VectorParams(size=m["dim"], distance=Distance.COSINE))
        points = [PointStruct(id=i, vector=vecs[i], payload={"text": texts[i], "source": chunks[i][1]}) for i in range(len(texts))]
        qd.upsert(col, points)

        results = qd.query_points(collection_name=col, query=qvec, limit=top_k, with_payload=True).points
        all_results[m["label"]] = [(r.id, r.score, r.payload) for r in results]
        qd.delete_collection(col)

# ── Display results ────────────────────────────────────────────────────────
st.subheader("Results by model")
model_cols = st.columns(len(all_results))

for col_ui, (label, results) in zip(model_cols, all_results.items()):
    with col_ui:
        st.markdown(f"**{label}**")
        for rank, (rid, score, payload) in enumerate(results, 1):
            color = "green" if score >= 0.7 else "orange" if score >= 0.4 else "red"
            with st.expander(f"#{rank} :{color}[{score:.4f}]  {payload.get('source','?')[:25]}"):
                st.write(payload.get("text", "")[:300])

# ── Overlap analysis ────────────────────────────────────────────────────────
if len(all_results) > 1:
    st.subheader("Result overlap matrix")
    labels = list(all_results.keys())
    matrix = []
    for l1 in labels:
        row = []
        ids1 = {r[0] for r in all_results[l1]}
        for l2 in labels:
            ids2 = {r[0] for r in all_results[l2]}
            overlap = len(ids1 & ids2) / max(len(ids1), len(ids2))
            row.append(f"{overlap:.0%}")
        matrix.append(row)
    df = pd.DataFrame(matrix, index=labels, columns=labels)
    st.dataframe(df, use_container_width=True)
    st.caption(
        "100% = same chunks retrieved. Low overlap means models rank different "
        "content as relevant — neither is necessarily wrong, but you should "
        "evaluate both against a ground truth dataset."
    )

with st.expander("Embedding model selection guide"):
    st.markdown("""
    **Changing models requires full re-indexing — this is embedding drift.**

    Production checklist when switching models:
    1. Create a new collection (new name, correct dim for new model)
    2. Re-embed entire corpus with new model
    3. Validate recall on your ground truth Q&A dataset
    4. If recall improves: swap the collection name (aliases) and delete the old one
    5. Update your application's embedding model config

    **Never query a collection with a different model than was used to index it.**
    The vector spaces are incompatible — you'll get silently wrong results.
    """)
