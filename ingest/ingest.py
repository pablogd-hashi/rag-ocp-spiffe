"""Ingest Markdown, HCL, and Terraform files into Qdrant."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from chunker import chunk_hcl, chunk_markdown

# ── Config from environment ─────────────────────────────────────────────
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION = os.environ.get("COLLECTION", "platform-docs")
DOCS_PATH = os.environ.get("DOCS_PATH", "/docs")

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "30"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "2"))

EMBED_DIM = 768  # nomic-embed-text output dimensionality


# ── Readiness helpers ───────────────────────────────────────────────────
def wait_for_qdrant(client: QdrantClient) -> None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client.get_collections()
            print("✓ Qdrant is ready")
            return
        except Exception as exc:
            print(f"  waiting for Qdrant ({attempt}/{MAX_RETRIES}): {exc}")
            time.sleep(RETRY_DELAY)
    sys.exit("Qdrant did not become ready in time")


def wait_for_ollama(ol: ollama.Client) -> None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ol.list()
            print("✓ Ollama is ready")
            return
        except Exception as exc:
            print(f"  waiting for Ollama ({attempt}/{MAX_RETRIES}): {exc}")
            time.sleep(RETRY_DELAY)
    sys.exit("Ollama did not become ready in time")


# ── File discovery and chunking ─────────────────────────────────────────
EXTENSIONS = {".md", ".hcl", ".tf"}


def discover_files(root: str) -> list[Path]:
    root_path = Path(root)
    return sorted(
        p for p in root_path.rglob("*") if p.suffix in EXTENSIONS and p.is_file()
    )


def chunk_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        return chunk_markdown(text)
    return chunk_hcl(text)


# ── Main ────────────────────────────────────────────────────────────────
def main() -> None:
    docs_root = Path(DOCS_PATH)
    if not docs_root.is_dir():
        sys.exit(f"DOCS_PATH {DOCS_PATH} is not a directory")

    # Clients
    qdrant_kwargs: dict = {"url": QDRANT_URL, "timeout": 30}
    if QDRANT_API_KEY:
        qdrant_kwargs["api_key"] = QDRANT_API_KEY
    qd = QdrantClient(**qdrant_kwargs)
    ol = ollama.Client(host=OLLAMA_URL)

    # Wait for services
    wait_for_qdrant(qd)
    wait_for_ollama(ol)

    # Ensure collection
    if not qd.collection_exists(COLLECTION):
        qd.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        print(f"✓ Created collection '{COLLECTION}'")
    else:
        print(f"✓ Collection '{COLLECTION}' already exists")

    # Discover and ingest
    files = discover_files(DOCS_PATH)
    if not files:
        sys.exit(f"No .md / .hcl / .tf files found under {DOCS_PATH}")

    point_id = 0
    points: list[PointStruct] = []

    for fpath in files:
        rel = str(fpath.relative_to(docs_root))
        chunks = chunk_file(fpath)
        print(f"  {rel}: {len(chunks)} chunk(s)")

        for chunk in chunks:
            resp = ol.embed(model=EMBED_MODEL, input=chunk)
            vector = resp.embeddings[0]
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={"text": chunk, "source": rel},
                )
            )
            point_id += 1

    qd.upsert(collection_name=COLLECTION, points=points)
    print(f"✓ Ingested {point_id} chunks from {len(files)} files")


if __name__ == "__main__":
    main()
