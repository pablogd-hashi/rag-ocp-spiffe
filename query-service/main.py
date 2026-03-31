"""FastAPI query service — retrieves context from Qdrant and answers with an LLM."""

from __future__ import annotations

import os
from typing import List

import ollama
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient

# ── Config ───────────────────────────────────────────────────────────────
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
LLM_MODEL = os.environ.get("LLM_MODEL", "phi3")
COLLECTION = os.environ.get("COLLECTION", "platform-docs")
TOP_K = int(os.environ.get("TOP_K", "5"))

# ── Clients ──────────────────────────────────────────────────────────────
qdrant_kwargs: dict = {"url": QDRANT_URL, "timeout": 30}
if QDRANT_API_KEY:
    qdrant_kwargs["api_key"] = QDRANT_API_KEY
qd = QdrantClient(**qdrant_kwargs)
ol = ollama.Client(host=OLLAMA_URL)

# ── FastAPI ──────────────────────────────────────────────────────────────
app = FastAPI(title="RAG Query Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    sources: List[str]
    top_score: float


SYSTEM_PROMPT = (
    "You are a helpful platform engineering assistant. "
    "Answer the user's question using ONLY the provided context. "
    "If the context does not contain enough information to answer, "
    "say exactly: 'The available documents do not cover this topic.' "
    "Do not make up information."
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    # Embed the question
    embed_resp = ol.embed(model=EMBED_MODEL, input=req.question)
    query_vector = embed_resp.embeddings[0]

    # Retrieve from Qdrant
    results = qd.query_points(
        collection_name=COLLECTION,
        query=query_vector,
        limit=TOP_K,
        with_payload=True,
    ).points

    if not results:
        return AskResponse(
            answer="No documents have been ingested yet.",
            sources=[],
            top_score=0.0,
        )

    top_score = results[0].score

    # Build context and deduplicated sources (order-preserved)
    context_parts: list[str] = []
    seen_sources: set[str] = set()
    sources: list[str] = []
    for r in results:
        context_parts.append(r.payload["text"])
        src = r.payload["source"]
        if src not in seen_sources:
            seen_sources.add(src)
            sources.append(src)

    context_block = "\n---\n".join(context_parts)

    # Ask the LLM
    llm_resp = ol.chat(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Context:\n{context_block}\n\n"
                    f"Question: {req.question}"
                ),
            },
        ],
    )

    return AskResponse(
        answer=llm_resp.message.content,
        sources=sources,
        top_score=round(top_score, 4),
    )
