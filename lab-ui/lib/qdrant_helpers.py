"""Shared Qdrant helpers for the lab UI."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    url     = os.environ.get("QDRANT_URL", "http://localhost:6333")
    api_key = os.environ.get("QDRANT_API_KEY", "") or None
    return QdrantClient(url=url, api_key=api_key, cloud_inference=True, timeout=30, check_compatibility=False)


def collection_names() -> list[str]:
    return [c.name for c in get_client().get_collections().collections]


def collection_summary(name: str) -> dict:
    qd   = get_client()
    info = qd.get_collection(name)
    cfg  = info.config.params
    hnsw = info.config.hnsw_config

    vec = cfg.vectors
    if hasattr(vec, "size"):
        vec_size = vec.size
        distance = str(vec.distance).split(".")[-1]
    else:
        vec_size = "named"
        distance = "named"

    return {
        "points":          info.points_count or 0,
        "indexed_vectors": info.indexed_vectors_count or 0,
        "segments":        info.segments_count or 0,
        "optimizer":       str(info.optimizer_status).split(".")[-1],
        "vec_size":        vec_size,
        "distance":        distance,
        "hnsw_m":          hnsw.m,
        "hnsw_ef_c":       hnsw.ef_construct,
        "hnsw_on_disk":    hnsw.on_disk,
        "quantization":    str(info.config.quantization_config).split("(")[0] if info.config.quantization_config else "None",
        "payload_schema":  {k: str(v.data_type).split(".")[-1] for k, v in (info.payload_schema or {}).items()},
    }


def scroll_points(collection: str, limit: int = 20,
                  tenant: Optional[str] = None,
                  doc_type: Optional[str] = None) -> list[dict]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    conditions = []
    if tenant:
        conditions.append(FieldCondition(key="tenant", match=MatchValue(value=tenant)))
    if doc_type:
        conditions.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type)))
    f = Filter(must=conditions) if conditions else None
    points, _ = get_client().scroll(
        collection_name=collection,
        scroll_filter=f,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return [{"id": p.id, **p.payload} for p in points]


def query_collection(collection: str, vector: list[float],
                     top_k: int = 5, score_threshold: float = 0.0,
                     tenant: Optional[str] = None,
                     doc_type: Optional[str] = None,
                     hnsw_ef: Optional[int] = None,
                     exact: bool = False) -> list[dict]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue, SearchParams
    conditions = []
    if tenant:
        conditions.append(FieldCondition(key="tenant", match=MatchValue(value=tenant)))
    if doc_type:
        conditions.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type)))
    f = Filter(must=conditions) if conditions else None

    sp = SearchParams(hnsw_ef=hnsw_ef, exact=exact) if (hnsw_ef or exact) else None

    results = get_client().query_points(
        collection_name=collection,
        query=vector,
        query_filter=f,
        limit=top_k,
        score_threshold=score_threshold if score_threshold > 0 else None,
        with_payload=True,
        search_params=sp,
    ).points
    return [{"id": r.id, "score": r.score, **r.payload} for r in results]
