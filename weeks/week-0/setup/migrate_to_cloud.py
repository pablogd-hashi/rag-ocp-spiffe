"""Week 0 — Migrate existing docs to Qdrant Cloud with enhanced payloads.

Uses Qdrant Cloud inference for embedding — no local Ollama required.

What this does:
  1. Reads all .md / .hcl / .tf files from docs/
  2. Chunks them (same logic as ingest/ingest.py)
  3. Sends chunks to Qdrant Cloud which embeds them server-side
  4. Upserts to Qdrant Cloud with tenant + doc_type payload fields
  5. Creates payload indexes on tenant and doc_type
  6. Prints collection stats

Run:  python weeks/week-0/setup/migrate_to_cloud.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Document,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────
QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
COLLECTION     = os.environ.get("COLLECTION", "platform-docs")
TENANT         = os.environ.get("TENANT", "default")
EMBED_DIM      = int(os.environ.get("EMBED_DIM", "384"))

# Resolve docs/ relative to repo root (three levels up from this script)
REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_PATH = REPO_ROOT / "docs"

# Chunker lives in ingest/
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from chunker import chunk_hcl, chunk_markdown


_DOC_TYPE_MAP = {
    "architecture":  "architecture",
    "runbooks":      "runbook",
    "policies":      "policy",
    "configuration": "configuration",
    "jobs":          "job",
}


def doc_type(rel: str) -> str:
    for seg, dt in _DOC_TYPE_MAP.items():
        if seg in rel:
            return dt
    return "general"


def stable_id(text: str, source: str) -> int:
    h = hashlib.sha256(f"{source}::{text[:200]}".encode()).hexdigest()
    return int(h[:16], 16) % (2**63)


def chunk_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return chunk_markdown(text) if path.suffix == ".md" else chunk_hcl(text)


def main() -> None:
    print(f"\nMigrating docs to Qdrant Cloud (server-side inference)")
    print(f"  URL        : {QDRANT_URL}")
    print(f"  Collection : {COLLECTION}")
    print(f"  Docs path  : {DOCS_PATH}")
    print(f"  Tenant     : {TENANT}")
    print(f"  Embed model: {EMBED_MODEL}  (Qdrant Cloud inference)")

    if not DOCS_PATH.is_dir():
        sys.exit(f"\n  ERROR: {DOCS_PATH} does not exist")

    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=60,
        check_compatibility=False,
    )

    # Create collection (delete + recreate to ensure clean state)
    if qd.collection_exists(COLLECTION):
        # Check if it's healthy — a broken collection returns 500 on get_collection
        is_broken = False
        try:
            qd.get_collection(COLLECTION)
        except Exception:
            is_broken = True

        if is_broken:
            print(f"\n  Collection '{COLLECTION}' is BROKEN — force-deleting ...")
        else:
            ans = input(f"\n  Collection '{COLLECTION}' exists. Delete and re-create? [y/N]: ")
            if ans.lower() != "y":
                print("  Aborting.")
                return

        try:
            qd.delete_collection(COLLECTION)
            print(f"  Deleted '{COLLECTION}'")
        except Exception as e:
            sys.exit(
                f"\n  ERROR: Could not delete '{COLLECTION}': {e}\n"
                "  Run: task w0:recover  (deletes ALL broken collections)\n"
            )

    qd.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
    )
    print(f"  Created collection '{COLLECTION}' (dim={EMBED_DIM}, cosine)")

    # Payload indexes — create BEFORE inserting data (cheaper if done early)
    for field in ("tenant", "doc_type"):
        qd.create_payload_index(
            collection_name=COLLECTION,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    print("  Created payload indexes: tenant, doc_type")

    # Discover + upsert — Qdrant Cloud embeds Document text server-side
    extensions = {".md", ".hcl", ".tf"}
    files = sorted(p for p in DOCS_PATH.rglob("*") if p.suffix in extensions and p.is_file())
    print(f"\n  Found {len(files)} files to ingest\n")

    points: list[PointStruct] = []
    total_chunks = 0

    for fpath in files:
        rel    = str(fpath.relative_to(DOCS_PATH))
        dtype  = doc_type(rel)
        chunks = chunk_file(fpath)
        total_chunks += len(chunks)
        print(f"  {rel:55s} {len(chunks):3d} chunks  [{dtype}]")

        for chunk in chunks:
            points.append(PointStruct(
                id=stable_id(chunk, rel),
                vector=Document(text=chunk, model=EMBED_MODEL),
                payload={
                    "text":     chunk,
                    "source":   rel,
                    "tenant":   TENANT,
                    "doc_type": dtype,
                },
            ))

    # Upsert in batches — Qdrant Cloud embeds each batch server-side
    batch_size = 100
    print(f"\n  Upserting {len(points)} points in batches of {batch_size} ...")
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        qd.upsert(collection_name=COLLECTION, points=batch)
        print(f"    {min(i+batch_size, len(points)):>5}/{len(points)}", end="\r")

    print(f"\n  ✓ Upserted {len(points)} chunks from {len(files)} files")

    # Final stats
    print("\n  Waiting 2s for optimizer ...")
    time.sleep(2)
    info = qd.get_collection(COLLECTION)
    print(f"\n  Collection stats:")
    print(f"    points_count        : {info.points_count:,}")
    print(f"    indexed_vectors     : {info.indexed_vectors_count:,}")
    print(f"    segments_count      : {info.segments_count}")
    print(f"    optimizer_status    : {info.optimizer_status}")
    print(f"\n  Done. Run: task w0:explore\n")


if __name__ == "__main__":
    main()
