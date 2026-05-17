"""Collection Explorer — browse collections, segments, config, raw points."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.qdrant_helpers import collection_names, collection_summary, get_client, scroll_points

st.set_page_config(page_title="Collection Explorer", layout="wide")
st.title("Collection Explorer")
st.caption("Week 0 — understand what a Qdrant collection is at the architecture level")

# ── Sidebar ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Controls")
    try:
        cols = collection_names()
    except Exception as e:
        st.error(f"Cannot connect: {e}")
        st.stop()

    if not cols:
        st.warning("No collections found. Run: task w0:migrate")
        st.stop()

    selected = st.selectbox("Collection", cols)

# ── Collection summary ────────────────────────────────────────────────────
st.subheader(f"Collection: `{selected}`")

try:
    summary = collection_summary(selected)
except Exception as e:
    st.error(f"Failed to get collection info: {e}")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total points",    f"{summary['points']:,}")
c2.metric("Indexed vectors", f"{summary['indexed_vectors']:,}")
c3.metric("Segments",        summary["segments"])
c4.metric("Optimizer",       summary["optimizer"])

gap = summary["points"] - summary["indexed_vectors"]
if gap > 0:
    st.warning(
        f"{gap:,} points not yet in HNSW graph — optimizer is building the index. "
        "Query recall may be slightly lower until indexing completes."
    )

# ── Vector and HNSW config ───────────────────────────────────────────────
st.subheader("Vector configuration")
col1, col2 = st.columns(2)

with col1:
    st.markdown("**Vector space**")
    st.json({
        "dimensions": summary["vec_size"],
        "distance":   summary["distance"],
    })

with col2:
    st.markdown("**HNSW index**")
    st.json({
        "m":                     summary["hnsw_m"],
        "ef_construct":          summary["hnsw_ef_c"],
        "on_disk":               summary["hnsw_on_disk"],
        "quantization":          summary["quantization"],
    })

with st.expander("HNSW parameter explainer"):
    st.markdown(f"""
    | Parameter | This collection | Meaning |
    |-----------|----------------|---------|
    | `m` | {summary["hnsw_m"]} | Connections per node per layer. Higher = better recall, more RAM. |
    | `ef_construct` | {summary["hnsw_ef_c"]} | Search width during index build. Higher = better graph, slower ingest. |
    | `on_disk` | {summary["hnsw_on_disk"]} | If True, graph edges stored on disk (lower RAM, higher latency). |
    | Memory (vec) | ~{summary["points"] * (summary["vec_size"] if isinstance(summary["vec_size"], int) else 768) * 4 / 1e6:.0f} MB | points × dims × 4 bytes |
    | Memory (graph) | ~{summary["points"] * summary["hnsw_m"] * 2 * 8 / 1e6:.0f} MB | points × m × 2 × 8 bytes |
    """)

# ── Payload schema ────────────────────────────────────────────────────────
st.subheader("Payload schema (indexed fields)")
if summary["payload_schema"]:
    df = pd.DataFrame([
        {"field": k, "type": v, "indexed": True}
        for k, v in summary["payload_schema"].items()
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        "These fields have payload indexes — filters on them use an inverted index (fast). "
        "Unindexed fields are filtered via full scan (slow at scale)."
    )
else:
    st.warning("No payload indexes found. Filters will do full scans.")
    st.code("qd.create_payload_index(collection, 'tenant', PayloadSchemaType.KEYWORD)")

# ── All collections table ─────────────────────────────────────────────────
st.subheader("All collections")
rows = []
for c in cols:
    try:
        s = collection_summary(c)
        rows.append({
            "Collection": c,
            "Points": s["points"],
            "Indexed": s["indexed_vectors"],
            "Segments": s["segments"],
            "Dim": s["vec_size"],
            "Distance": s["distance"],
            "m": s["hnsw_m"],
            "ef_c": s["hnsw_ef_c"],
            "Quantization": s["quantization"],
            "Optimizer": s["optimizer"],
        })
    except Exception:
        pass
if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── Raw point browser ─────────────────────────────────────────────────────
st.subheader("Browse raw points (scroll — no query)")
with st.sidebar:
    st.markdown("---")
    st.markdown("**Point browser filters**")
    tenant_filter   = st.text_input("Filter by tenant", "")
    doc_type_filter = st.text_input("Filter by doc_type", "")
    scroll_limit    = st.slider("Points to show", 5, 50, 10)

try:
    points = scroll_points(
        selected,
        limit=scroll_limit,
        tenant=tenant_filter or None,
        doc_type=doc_type_filter or None,
    )
    if points:
        df = pd.DataFrame(points)
        # Truncate text for display
        if "text" in df.columns:
            df["text"] = df["text"].str[:200] + "..."
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(
            f"Showing {len(points)} points. "
            "This uses the Qdrant scroll API — iterates points without a query vector. "
            "Useful for debugging payloads and verifying ingest."
        )
    else:
        st.info("No points matched the filters.")
except Exception as e:
    st.error(f"Scroll failed: {e}")
