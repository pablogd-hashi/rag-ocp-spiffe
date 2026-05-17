"""FastAPI query service — retrieves context from Qdrant and answers with an LLM.

Embedding: Qdrant Cloud inference (server-side, no local Ollama needed for embed).
LLM:       Ollama (local phi3 or configured model).

Endpoints:
  GET  /health            — liveness probe
  GET  /collections       — list all collections with point counts
  GET  /collection-info   — full stats for the active collection
  GET  /scroll            — browse raw points (no query, useful for debugging)
  POST /ask               — RAG answer; supports optional ?tenant= filter
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import ollama
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Document, FieldCondition, Filter, MatchValue

# ── Config ────────────────────────────────────────────────────────────────
QDRANT_URL     = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
LLM_MODEL      = os.environ.get("LLM_MODEL", "phi3")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")
TOP_K          = int(os.environ.get("TOP_K", "5"))

# ── Clients ───────────────────────────────────────────────────────────────
qd = QdrantClient(
    url=QDRANT_URL,
    api_key=QDRANT_API_KEY or None,
    cloud_inference=True,
    timeout=30,
    check_compatibility=False,
)
ol = ollama.Client(host=OLLAMA_URL)

# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="RAG Query Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM_PROMPT = (
    "You are a helpful platform engineering assistant. "
    "Answer the user's question using ONLY the provided context. "
    "If the context does not contain enough information, say: "
    "'The available documents do not cover this topic.' "
    "Do not make up information."
)


# ── Models ────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str
    tenant:   Optional[str] = None
    doc_type: Optional[str] = None
    top_k:    Optional[int] = None


class AskResponse(BaseModel):
    answer:      str
    sources:     List[str]
    top_score:   float
    chunks_used: int


# ── Endpoints ─────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/collections")
def list_collections():
    """List every collection in the cluster with its point count."""
    cols = qd.get_collections().collections
    return [
        {
            "name":   c.name,
            "points": qd.get_collection(c.name).points_count,
        }
        for c in cols
    ]


@app.get("/collection-info")
def collection_info(collection: str = COLLECTION):
    """
    Returns full stats for a collection:
      segments_count      — grows during heavy ingest, shrinks as optimizer merges
      indexed_vectors     — vectors in the HNSW graph (non-indexed = not yet graphed)
      optimizer_status    — ok | error | optimizing
      hnsw_config.m       — connections per node; higher = better recall, more memory
      hnsw_config.ef_construct — search width during index build
    """
    info = qd.get_collection(collection)
    cfg  = info.config.params
    hnsw = info.config.hnsw_config

    return {
        "name":             collection,
        "points_count":     info.points_count,
        "segments_count":   info.segments_count,
        "indexed_vectors":  info.indexed_vectors_count,
        "optimizer_status": str(info.optimizer_status),
        "vector_size":      cfg.vectors.size if hasattr(cfg.vectors, "size") else "named",
        "distance":         str(cfg.vectors.distance) if hasattr(cfg.vectors, "distance") else "named",
        "hnsw_config": {
            "m":                  hnsw.m,
            "ef_construct":       hnsw.ef_construct,
            "full_scan_threshold": hnsw.full_scan_threshold,
            "on_disk":            hnsw.on_disk,
        },
        "quantization": str(info.config.quantization_config) if info.config.quantization_config else None,
        "payload_schema": {
            k: str(v.data_type)
            for k, v in (info.payload_schema or {}).items()
        },
    }


@app.get("/scroll")
def scroll_points(
    collection: str = COLLECTION,
    limit: int = Query(default=10, le=100),
    offset: Optional[str] = None,
    tenant: Optional[str] = None,
    doc_type: Optional[str] = None,
):
    """Browse raw points without running a query."""
    conditions = []
    if tenant:
        conditions.append(FieldCondition(key="tenant", match=MatchValue(value=tenant)))
    if doc_type:
        conditions.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type)))

    scroll_filter = Filter(must=conditions) if conditions else None

    points, next_offset = qd.scroll(
        collection_name=collection,
        scroll_filter=scroll_filter,
        limit=limit,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )

    return {
        "points": [{"id": p.id, "payload": p.payload} for p in points],
        "next_offset": str(next_offset) if next_offset else None,
    }


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    top_k = req.top_k or TOP_K

    # Build optional filter
    conditions = []
    if req.tenant:
        conditions.append(FieldCondition(key="tenant", match=MatchValue(value=req.tenant)))
    if req.doc_type:
        conditions.append(FieldCondition(key="doc_type", match=MatchValue(value=req.doc_type)))

    query_filter = Filter(must=conditions) if conditions else None

    # Qdrant Cloud embeds the question server-side — no local embed call needed
    results = qd.query_points(
        collection_name=COLLECTION,
        query=Document(text=req.question, model=EMBED_MODEL),
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    ).points

    if not results:
        return AskResponse(
            answer="No documents matched your query (check tenant/doc_type filters).",
            sources=[],
            top_score=0.0,
            chunks_used=0,
        )

    context_parts: list[str] = []
    seen: set[str] = set()
    sources: list[str] = []
    for r in results:
        context_parts.append(r.payload["text"])
        src = r.payload.get("source", "unknown")
        if src not in seen:
            seen.add(src)
            sources.append(src)

    llm_resp = ol.chat(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{chr(10)+'---'+chr(10).join(context_parts)}\n\nQuestion: {req.question}"},
        ],
    )

    return AskResponse(
        answer=llm_resp.message.content,
        sources=sources,
        top_score=round(results[0].score, 4),
        chunks_used=len(results),
    )
