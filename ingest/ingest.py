"""Ingest Markdown, HCL, and Terraform files into Qdrant.

Uses Qdrant Cloud inference for embedding — no local Ollama required.
Payloads include tenant, doc_type, and week tags so the learning-lab
experiments can filter by these fields without touching collection design.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Document,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from chunker import chunk_hcl, chunk_markdown

# ── Config ──────────────────────────────────────────────────────────────
QDRANT_URL     = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")
DOCS_PATH      = os.environ.get("DOCS_PATH", "/docs")
TENANT         = os.environ.get("TENANT", "default")
EMBED_DIM      = int(os.environ.get("EMBED_DIM", "384"))

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "30"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "2"))

# Map file paths to human-readable doc_type values for payload filtering
_DOC_TYPE_MAP = {
    "architecture": "architecture",
    "runbooks":     "runbook",
    "policies":     "policy",
    "configuration":"configuration",
    "jobs":         "job",
}


def _doc_type(rel_path: str) -> str:
    for segment, dtype in _DOC_TYPE_MAP.items():
        if segment in rel_path:
            return dtype
    return "general"


def _stable_id(text: str, source: str) -> int:
    """Deterministic point ID so re-ingestion is idempotent."""
    h = hashlib.sha256(f"{source}::{text[:200]}".encode()).hexdigest()
    return int(h[:16], 16) % (2**63)


# ── Readiness helpers ────────────────────────────────────────────────────
def wait_for_qdrant(client: QdrantClient) -> None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client.get_collections()
            print("✓ Qdrant ready")
            return
        except Exception as exc:
            print(f"  waiting for Qdrant ({attempt}/{MAX_RETRIES}): {exc}")
            time.sleep(RETRY_DELAY)
    sys.exit("Qdrant did not become ready in time")


# ── File discovery ───────────────────────────────────────────────────────
EXTENSIONS = {".md", ".hcl", ".tf"}


def discover_files(root: str) -> list[Path]:
    root_path = Path(root)
    return sorted(p for p in root_path.rglob("*") if p.suffix in EXTENSIONS and p.is_file())


def chunk_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return chunk_markdown(text) if path.suffix == ".md" else chunk_hcl(text)


# ── Collection bootstrap ─────────────────────────────────────────────────
def ensure_collection(qd: QdrantClient) -> None:
    if not qd.collection_exists(COLLECTION):
        qd.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        print(f"✓ Created collection '{COLLECTION}' (dim={EMBED_DIM}, cosine)")
    else:
        print(f"✓ Collection '{COLLECTION}' already exists")

    # Payload indexes let Qdrant filter without scanning all points.
    for field in ("tenant", "doc_type"):
        try:
            qd.create_payload_index(
                collection_name=COLLECTION,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            print(f"  payload index: {field} (keyword)")
        except Exception:
            pass  # index already exists


# ── Main ─────────────────────────────────────────────────────────────────
def main() -> None:
    docs_root = Path(DOCS_PATH)
    if not docs_root.is_dir():
        sys.exit(f"DOCS_PATH {DOCS_PATH} is not a directory")

    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=30,
        check_compatibility=False,
    )

    wait_for_qdrant(qd)
    ensure_collection(qd)

    files = discover_files(DOCS_PATH)
    if not files:
        sys.exit(f"No .md / .hcl / .tf files found under {DOCS_PATH}")

    points: list[PointStruct] = []
    for fpath in files:
        rel   = str(fpath.relative_to(docs_root))
        dtype = _doc_type(rel)
        chunks = chunk_file(fpath)
        print(f"  {rel}: {len(chunks)} chunk(s)  [doc_type={dtype}]")

        for chunk in chunks:
            points.append(
                PointStruct(
                    id=_stable_id(chunk, rel),
                    vector=Document(text=chunk, model=EMBED_MODEL),
                    payload={
                        "text":     chunk,
                        "source":   rel,
                        "tenant":   TENANT,
                        "doc_type": dtype,
                    },
                )
            )

    # Upsert in batches of 100
    batch_size = 100
    for i in range(0, len(points), batch_size):
        qd.upsert(collection_name=COLLECTION, points=points[i : i + batch_size])

    print(f"✓ Ingested {len(points)} chunks from {len(files)} files  (tenant={TENANT})")


if __name__ == "__main__":
    main()
