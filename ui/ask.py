"""Streamlit UI for the RAG platform query service."""

import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Platform Docs Q&A", page_icon="📚")
st.title("📚 Platform Docs Q&A")

question = st.text_input("Ask a question about your platform docs:")

if question:
    try:
        resp = requests.post(f"{API_URL}/ask", json={"question": question}, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        st.markdown("### Answer")
        st.write(data["answer"])

        score = data["top_score"]
        colour = "green" if score >= 0.5 else "orange"
        st.markdown(
            f"**Retrieval score:** :{colour}[{score:.4f}]"
        )
        if score < 0.5:
            st.warning(
                "Score below 0.5 — the index may be stale or the question is out of scope."
            )

        if data["sources"]:
            st.markdown("### Sources")
            for src in data["sources"]:
                st.markdown(f"- `{src}`")
    except requests.exceptions.ConnectionError:
        st.error("Could not reach the query service. Is it running?")
    except Exception as exc:
        st.error(f"Error: {exc}")
