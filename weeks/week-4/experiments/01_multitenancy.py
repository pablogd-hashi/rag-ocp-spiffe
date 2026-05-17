"""Week 4 — Lab 1: Multi-tenancy simulation — payload filter vs per-collection.

Uses Qdrant Cloud inference for embedding — no local Ollama needed.

Demonstrates Pattern A (one collection, filter by tenant payload) and
Pattern B (one collection per tenant) side by side.

Key comparisons:
  - Isolation: how do you delete all data for one tenant?
  - Recall: does a small tenant in a large shared collection lose quality?
  - Operations: what breaks at scale?

Run:  python weeks/week-4/experiments/01_multitenancy.py
"""

from __future__ import annotations

import hashlib
import os
import time
from statistics import mean

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Document,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "sentence-transformers/all-minilm-l6-v2")
DIM            = int(os.environ.get("EMBED_DIM", "384"))

COL_A  = "w4-pattern-a-shared"
COL_B1 = "w4-pattern-b-platform"
COL_B2 = "w4-pattern-b-security"

TENANTS = {
    "platform": [
        "Consul Connect uses mTLS for all service-to-service communication.",
        "Vault PKI issues short-lived certificates for the Consul CA.",
        "OCP service accounts map to Vault roles via Kubernetes auth.",
        "Namespace rag-platform hosts the query service and UI.",
        "The ingest pipeline reads docs/ and upserts vectors to Qdrant.",
        "Ollama runs embedding models on port 11434.",
    ],
    "security": [
        "SPIFFE IDs follow the format spiffe://trust-domain/ns/namespace/sa/name.",
        "mTLS mutual authentication requires both client and server certificates.",
        "Vault policies restrict access to PKI paths by service account.",
        "Certificate rotation is automated via Vault Agent Injector.",
        "Consul intentions deny all traffic by default — allow-lists required.",
    ],
}

TEST_QUERIES = {
    "platform": "How does the ingest pipeline work?",
    "security": "What format does a SPIFFE ID use?",
}


def stable_id(tenant: str, text: str) -> int:
    return int(hashlib.sha256(f"{tenant}::{text[:100]}".encode()).hexdigest()[:16], 16) % (2**63)


def ensure_collection(qd: QdrantClient, name: str) -> None:
    if qd.collection_exists(name):
        qd.delete_collection(name)
    qd.create_collection(name, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))
    qd.create_payload_index(name, "tenant", PayloadSchemaType.KEYWORD)


def ingest_pattern_a(qd: QdrantClient) -> None:
    ensure_collection(qd, COL_A)
    points = []
    for tenant, docs in TENANTS.items():
        for doc in docs:
            points.append(PointStruct(
                id=stable_id(tenant, doc),
                vector=Document(text=doc, model=EMBED_MODEL),
                payload={"text": doc, "tenant": tenant},
            ))
    qd.upsert(COL_A, points)
    print(f"  Pattern A ({COL_A}): {len(points)} points ingested")


def ingest_pattern_b(qd: QdrantClient) -> None:
    for col, (tenant, docs) in zip([COL_B1, COL_B2], TENANTS.items()):
        ensure_collection(qd, col)
        points = [
            PointStruct(
                id=stable_id(tenant, doc),
                vector=Document(text=doc, model=EMBED_MODEL),
                payload={"text": doc, "tenant": tenant},
            )
            for doc in docs
        ]
        qd.upsert(col, points)
        print(f"  Pattern B ({col}): {len(points)} points ingested")


def query_pattern_a(qd: QdrantClient, tenant: str) -> tuple[list, float]:
    t0 = time.perf_counter()
    results = qd.query_points(
        collection_name=COL_A,
        query=Document(text=TEST_QUERIES[tenant], model=EMBED_MODEL),
        query_filter=Filter(must=[FieldCondition(key="tenant", match=MatchValue(value=tenant))]),
        limit=3,
        with_payload=True,
    ).points
    return results, (time.perf_counter() - t0) * 1000


def query_pattern_b(qd: QdrantClient, tenant: str) -> tuple[list, float]:
    col = COL_B1 if tenant == "platform" else COL_B2
    t0 = time.perf_counter()
    results = qd.query_points(
        collection_name=col,
        query=Document(text=TEST_QUERIES[tenant], model=EMBED_MODEL),
        limit=3,
        with_payload=True,
    ).points
    return results, (time.perf_counter() - t0) * 1000


def cleanup(qd: QdrantClient) -> None:
    for col in [COL_A, COL_B1, COL_B2]:
        if qd.collection_exists(col):
            qd.delete_collection(col)
    print("  Scratch collections deleted.")


def main() -> None:
    qd = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        cloud_inference=True,
        timeout=30,
        check_compatibility=False,
    )

    print("\nIngesting tenant data via Qdrant Cloud inference ...")
    ingest_pattern_a(qd)
    ingest_pattern_b(qd)

    time.sleep(2)

    print(f"\n{'─'*60}")
    print("  Pattern A — Shared collection with payload filter")
    print("─" * 60)

    for tenant in TENANTS:
        results, lat = query_pattern_a(qd, tenant)
        print(f"\n  Tenant: {tenant!r}  query: {TEST_QUERIES[tenant]!r}")
        print(f"  Latency: {lat:.1f}ms")
        for r in results:
            wrong_tenant = r.payload.get("tenant") != tenant
            marker = " ⚠ WRONG TENANT" if wrong_tenant else ""
            print(f"    score={r.score:.3f}  tenant={r.payload['tenant']}  text={r.payload['text'][:60]}{marker}")

    print(f"\n{'─'*60}")
    print("  Pattern B — Per-tenant collection")
    print("─" * 60)

    for tenant in TENANTS:
        results, lat = query_pattern_b(qd, tenant)
        print(f"\n  Tenant: {tenant!r}  query: {TEST_QUERIES[tenant]!r}")
        print(f"  Latency: {lat:.1f}ms")
        for r in results:
            print(f"    score={r.score:.3f}  text={r.payload['text'][:60]}")

    print(f"""
Pattern comparison:

  Pattern A (shared collection + payload filter):
    ✓ Single HNSW graph — both tenants share graph quality
    ✓ Simpler ops — one collection to manage, monitor, backup
    ✓ Cheaper — no duplicate RAM for HNSW per tenant
    ✗ Payload index REQUIRED on "tenant" — without it O(N) scan per query
    ✗ Deleting a tenant: delete_points by filter (slow for large tenants)
    ✗ Cross-tenant data isolation is app-level only

  Pattern B (per-tenant collection):
    ✓ Complete storage isolation — delete tenant = delete collection
    ✓ Per-tenant HNSW tuning possible
    ✗ N collections × RAM per HNSW index
    ✗ Qdrant Cloud free tier has collection limits
    ✗ Monitoring: N collections to alert on, not 1

  Interview answer:
    "< 100 tenants, similar volumes: Pattern A + payload index.
    Enterprise multi-tenant SaaS with strict isolation: Pattern B.
    Hybrid: start with A, migrate high-value tenants to B individually."

  → Qdrant UI: Collections → w4-pattern-a-shared → Points → filter tenant=platform
""")

    cleanup(qd)


if __name__ == "__main__":
    main()
