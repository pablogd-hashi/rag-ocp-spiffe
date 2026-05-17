"""Qdrant Learning Lab — main entry point.

A multi-page Streamlit app for hands-on Qdrant experimentation.
Each page corresponds to a learning goal from the study plan.

Run:  streamlit run lab-ui/app.py
      task lab
"""

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Qdrant Learning Lab",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Qdrant Learning Lab")
st.caption("Interactive experiments for the Solutions Engineer learning path")

st.markdown("""
## Navigation

Use the sidebar to switch between lab modules:

| Page | What you learn |
|------|---------------|
| **Collection Explorer** | Collections, segments, HNSW config, payload schema |
| **Query Lab** | ANN search, score thresholds, filters, ef tuning |
| **HNSW Tuner** | m vs ef_construct tradeoffs — recall and latency benchmark |
| **Embedding Compare** | Same query across different embedding models |
| **Retrieval Evaluator** | Rate retrieval quality with your own Q&A pairs |

## Quick connection check
""")

import os
from lib.qdrant_helpers import get_client, collection_names

url = os.environ.get("QDRANT_URL", "not set")
key = os.environ.get("QDRANT_API_KEY", "")

col1, col2 = st.columns(2)
col1.metric("Qdrant URL", url.split("//")[-1][:40] + "..." if len(url) > 45 else url)
col2.metric("API Key", "set" if key else "not set (local mode)")

try:
    cols = collection_names()
    st.success(f"Connected — {len(cols)} collection(s): {', '.join(cols) or 'none'}")
except Exception as e:
    st.error(f"Cannot connect to Qdrant: {e}")
    st.info("Check QDRANT_URL and QDRANT_API_KEY in your .env file")
