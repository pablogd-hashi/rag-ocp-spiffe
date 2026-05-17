"""Query Lab — run searches, tune parameters, compare results side by side."""

import os
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.qdrant_helpers import collection_names, get_client, query_collection

st.set_page_config(page_title="Query Lab", layout="wide")
st.title("Query Lab")
st.caption("Week 0–1 — ANN search, filters, ef tuning, score interpretation")

# ── Sidebar controls ──────────────────────────────────────────────────────
with st.sidebar:
    st.header("Search parameters")

    OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

    try:
        cols = collection_names()
    except Exception as e:
        st.error(f"Cannot connect: {e}")
        st.stop()

    collection     = st.selectbox("Collection", cols)
    top_k          = st.slider("top_k (results)", 1, 20, 5)
    score_min      = st.slider("Min score threshold", 0.0, 1.0, 0.0, 0.05)
    hnsw_ef        = st.select_slider("HNSW ef (query)", [32, 64, 128, 256, 512], value=128)
    exact_search   = st.checkbox("Exact search (brute force)", value=False,
                                 help="Bypasses HNSW — ground truth for recall measurement")

    st.markdown("---")
    st.markdown("**Payload filters**")
    tenant_filter   = st.text_input("tenant", "")
    doc_type_filter = st.text_input("doc_type", "")

    st.markdown("---")
    compare_mode = st.checkbox("Compare mode (A/B)", value=False,
                               help="Run same query on two different settings")
    if compare_mode:
        top_k_b       = st.slider("top_k B", 1, 20, 5)
        hnsw_ef_b     = st.select_slider("HNSW ef B", [32, 64, 128, 256, 512], value=64)
        exact_search_b = st.checkbox("Exact search B", value=True)

# ── Main ──────────────────────────────────────────────────────────────────
question = st.text_input(
    "Query",
    placeholder="How does Vault issue SPIFFE certificates?",
    help="This is embedded with Ollama and used as the ANN query vector",
)

if not question:
    st.info("Enter a question above to start searching.")
    st.stop()

# Embed via Ollama
try:
    import ollama as ol_lib
    ol = ol_lib.Client(host=OLLAMA_URL)
    t_embed = time.perf_counter()
    query_vector = ol.embed(model=EMBED_MODEL, input=question).embeddings[0]
    embed_ms = (time.perf_counter() - t_embed) * 1000
except Exception as e:
    st.error(f"Ollama embedding failed: {e}\nCheck OLLAMA_URL={OLLAMA_URL}")
    st.stop()

st.caption(f"Embedded with `{EMBED_MODEL}` in {embed_ms:.0f}ms — {len(query_vector)} dimensions")

# ── Run queries ───────────────────────────────────────────────────────────
def run_search(label: str, top_k: int, hnsw_ef: int, exact: bool):
    t0 = time.perf_counter()
    results = query_collection(
        collection=collection,
        vector=query_vector,
        top_k=top_k,
        score_threshold=score_min,
        tenant=tenant_filter or None,
        doc_type=doc_type_filter or None,
        hnsw_ef=hnsw_ef,
        exact=exact,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    return results, latency_ms

if compare_mode:
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader(f"A — ef={hnsw_ef}, exact={exact_search}")
        results_a, lat_a = run_search("A", top_k, hnsw_ef, exact_search)
        st.metric("Latency", f"{lat_a:.0f}ms")
        st.metric("Results", len(results_a))
        for r in results_a:
            with st.expander(f"score={r['score']:.4f}  {r.get('source','?')[:45]}"):
                st.write(r.get("text", ""))
                st.json({k: v for k, v in r.items() if k not in ("text",)})

    with col_b:
        st.subheader(f"B — ef={hnsw_ef_b}, exact={exact_search_b}")
        results_b, lat_b = run_search("B", top_k_b, hnsw_ef_b, exact_search_b)
        st.metric("Latency", f"{lat_b:.0f}ms")
        st.metric("Results", len(results_b))
        for r in results_b:
            with st.expander(f"score={r['score']:.4f}  {r.get('source','?')[:45]}"):
                st.write(r.get("text", ""))
                st.json({k: v for k, v in r.items() if k not in ("text",)})

    # Overlap analysis
    if results_a and results_b:
        ids_a = {r["id"] for r in results_a}
        ids_b = {r["id"] for r in results_b}
        overlap = len(ids_a & ids_b)
        recall  = overlap / min(len(ids_a), len(ids_b)) if ids_a or ids_b else 0.0
        st.markdown("---")
        st.subheader("Comparison")
        m1, m2, m3 = st.columns(3)
        m1.metric("Shared results", f"{overlap}")
        m2.metric("Recall A vs B", f"{recall:.0%}")
        m3.metric("Latency diff", f"{lat_a - lat_b:+.0f}ms")
        if exact_search_b and not exact_search:
            st.info(
                f"Recall of A (HNSW ef={hnsw_ef}) vs exact ground truth: "
                f"{recall:.1%}. "
                "This is how you measure HNSW recall degradation without an external eval dataset."
            )

else:
    results, lat = run_search("main", top_k, hnsw_ef, exact_search)

    m1, m2, m3 = st.columns(3)
    m1.metric("Query latency", f"{lat:.0f}ms")
    m2.metric("Results returned", len(results))
    m3.metric("Top score", f"{results[0]['score']:.4f}" if results else "—")

    if not results:
        st.warning(
            "No results returned. "
            "Check score threshold (lower it), or verify tenant/doc_type filters match your data."
        )
        st.stop()

    st.subheader("Results")
    for i, r in enumerate(results):
        score = r["score"]
        color = "green" if score >= 0.7 else "orange" if score >= 0.4 else "red"
        with st.expander(f"#{i+1}  score=:{color}[{score:.4f}]  {r.get('source','?')}", expanded=i == 0):
            st.write(r.get("text", ""))
            meta = {k: v for k, v in r.items() if k not in ("text", "score")}
            st.json(meta)

    # Score distribution
    scores = [r["score"] for r in results]
    df = pd.DataFrame({"result": range(1, len(scores)+1), "score": scores})
    st.bar_chart(df.set_index("result"))

    with st.expander("Score interpretation guide"):
        st.markdown("""
        | Score range | Interpretation |
        |-------------|----------------|
        | 0.9 – 1.0 | Near-identical semantics (or exact duplicate) |
        | 0.7 – 0.9 | Strongly related — good retrieval |
        | 0.5 – 0.7 | Moderately related — review carefully |
        | 0.3 – 0.5 | Loosely related — likely noise |
        | < 0.3 | Semantically unrelated — bad retrieval signal |

        **If all scores are below 0.5:** The query is semantically distant from
        your corpus. Causes: wrong embedding model, mismatched language, poor
        chunking, or corpus doesn't cover the topic.

        **If top score is 1.0:** You re-queried with an exact stored vector (self-query)
        or ingested duplicate content.
        """)
