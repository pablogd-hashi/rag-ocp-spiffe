"""Retrieval Evaluator — rate results, compute MRR@K and Precision@K."""

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.qdrant_helpers import collection_names, get_client, query_collection

st.set_page_config(page_title="Retrieval Evaluator", layout="wide")
st.title("Retrieval Evaluator")
st.caption("Week 3 — measure retrieval quality with MRR@K and Precision@K")

OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# ── Ground truth Q&A set ───────────────────────────────────────────────────
DEFAULT_QA = [
    {
        "question": "What is the SPIFFE ID format used in this platform?",
        "expected_keywords": ["spiffe://dc1", "ns/rag-platform", "svc/"],
        "notes": "Should return architecture/consul-connect-spiffe.md",
    },
    {
        "question": "How long is the leaf certificate TTL?",
        "expected_keywords": ["72h", "TTL", "leaf cert"],
        "notes": "Should return k8s/consul/consul-values.yaml or architecture doc",
    },
    {
        "question": "What Vault PKI paths are configured for Consul Connect?",
        "expected_keywords": ["connect_root", "connect_inter"],
        "notes": "Should return scripts/configure-vault.sh or architecture doc",
    },
    {
        "question": "What Consul service intention allows the UI to talk to the query service?",
        "expected_keywords": ["ui", "query-service", "allow"],
        "notes": "Should return k8s/consul/intentions.yaml",
    },
    {
        "question": "What embedding model is used and what are its dimensions?",
        "expected_keywords": ["nomic-embed-text", "768"],
        "notes": "Should return ingest or query-service docs",
    },
]

with st.sidebar:
    st.header("Eval config")
    try:
        cols = collection_names()
    except Exception as e:
        st.error(str(e))
        st.stop()
    collection = st.selectbox("Collection", cols)
    top_k      = st.slider("Retrieve top_k", 1, 20, 5)
    tenant_f   = st.text_input("tenant filter", "")
    doc_type_f = st.text_input("doc_type filter", "")
    run_all    = st.button("Evaluate all questions", type="primary")

st.markdown("""
### How to use

1. The default Q&A pairs are designed for your platform-docs corpus.
2. Click **Evaluate all questions** to run all queries.
3. For each result, rate whether it is **relevant** (thumb up) or **not relevant**.
4. The evaluator computes **MRR@K** and **Precision@K** from your ratings.

> **MRR@K (Mean Reciprocal Rank):** average of 1/rank_of_first_relevant_result.
> MRR=1.0 means the first result was always relevant. MRR=0.5 means the first
> relevant result was on average at position 2.

> **Precision@K:** fraction of retrieved results rated relevant.
""")

# ── State management ───────────────────────────────────────────────────────
if "ratings" not in st.session_state:
    st.session_state.ratings = {}  # {(question, result_id): bool}
if "eval_results" not in st.session_state:
    st.session_state.eval_results = {}  # {question: [result dicts]}

def embed_question(q: str) -> list[float]:
    import ollama
    ol = ollama.Client(host=OLLAMA_URL)
    return ol.embed(model=EMBED_MODEL, input=q).embeddings[0]

if run_all:
    with st.spinner("Running all queries ..."):
        for qa in DEFAULT_QA:
            q = qa["question"]
            try:
                vec = embed_question(q)
                results = query_collection(
                    collection=collection,
                    vector=vec,
                    top_k=top_k,
                    tenant=tenant_f or None,
                    doc_type=doc_type_f or None,
                )
                st.session_state.eval_results[q] = results
            except Exception as e:
                st.error(f"Query failed for '{q}': {e}")

# ── Show results and collect ratings ──────────────────────────────────────
total_mrr    = []
total_prec   = []

for qa in DEFAULT_QA:
    q       = qa["question"]
    results = st.session_state.eval_results.get(q, [])

    with st.expander(f"Q: {q}", expanded=bool(results)):
        st.caption(f"Expected: {qa['notes']}")
        if not results:
            st.info("Not yet evaluated. Click 'Evaluate all questions'.")
            continue

        for i, r in enumerate(results):
            key = (q, r["id"])
            c1, c2 = st.columns([5, 1])
            with c1:
                text_preview = r.get("text", "")[:200]
                src  = r.get("source", "?")
                score = r.get("score", 0)
                # Auto-rate based on keywords (pre-fill, user can override)
                auto = any(kw.lower() in r.get("text","").lower() for kw in qa["expected_keywords"])
                st.markdown(f"**#{i+1}** score={score:.4f}  `{src}`")
                st.write(text_preview)
            with c2:
                default = st.session_state.ratings.get(key, auto)
                rating  = st.checkbox("Relevant", value=default, key=f"rate_{q}_{r['id']}")
                st.session_state.ratings[key] = rating

        # Compute metrics for this question
        rated = [(r, st.session_state.ratings.get((q, r["id"]), False)) for r in results]
        relevant_ranks = [i+1 for i, (_, rel) in enumerate(rated) if rel]
        mrr   = (1.0 / relevant_ranks[0]) if relevant_ranks else 0.0
        prec  = sum(1 for _, rel in rated if rel) / len(rated)
        total_mrr.append(mrr)
        total_prec.append(prec)

        m1, m2 = st.columns(2)
        m1.metric(f"MRR@{top_k}", f"{mrr:.2f}")
        m2.metric(f"Precision@{top_k}", f"{prec:.0%}")

# ── Aggregate metrics ──────────────────────────────────────────────────────
if total_mrr:
    st.markdown("---")
    st.subheader("Aggregate evaluation")
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Mean MRR@{top_k}",        f"{sum(total_mrr)/len(total_mrr):.3f}")
    c2.metric(f"Mean Precision@{top_k}",   f"{sum(total_prec)/len(total_prec):.0%}")
    c3.metric("Questions evaluated",       f"{len(total_mrr)}/{len(DEFAULT_QA)}")

    with st.expander("Interpreting these metrics"):
        st.markdown(f"""
        **MRR@{top_k} interpretation:**
        | Score | Meaning |
        |-------|---------|
        | 1.0 | First result always relevant — excellent |
        | 0.5 | First relevant result at position 2 on average — good |
        | 0.33 | First relevant result at position 3 — acceptable |
        | < 0.2 | Retrieval quality poor — investigate chunking and embedding model |

        **Common causes of low MRR:**
        - Chunk size too large (dilutes the semantic signal)
        - Wrong embedding model for your domain
        - Missing payload filter (wrong tenant/doc_type showing up)
        - Collection not fully indexed (check indexed_vectors on Collection Explorer)
        - Query phrasing doesn't match document phrasing (try paraphrasing)

        **What to do with this data:**
        1. Note which questions have MRR < 0.3
        2. Inspect those query results — are the low-ranked chunks semantically related?
        3. If yes: recall is fine but ranking is off → try reranking
        4. If no: wrong chunks retrieved → fix chunking or embedding model
        """)

    # Export
    export_rows = []
    for qa in DEFAULT_QA:
        q = qa["question"]
        results = st.session_state.eval_results.get(q, [])
        for i, r in enumerate(results):
            export_rows.append({
                "question": q,
                "rank": i+1,
                "score": r.get("score", 0),
                "source": r.get("source", "?"),
                "relevant": st.session_state.ratings.get((q, r["id"]), False),
            })
    if export_rows:
        df = pd.DataFrame(export_rows)
        st.download_button(
            "Export ratings as CSV",
            df.to_csv(index=False),
            file_name="retrieval_eval.csv",
            mime="text/csv",
        )
