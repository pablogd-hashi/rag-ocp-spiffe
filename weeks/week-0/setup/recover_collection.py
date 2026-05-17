"""Force-delete ALL broken collections and report cluster health.

Run this when the Qdrant UI shows 'Failed to restore local replica',
or when task w0:explore reports ⚠ BROKEN collections.

This script:
  1. Lists all collections
  2. Tries get_collection() on each — a 500 error means it's broken
  3. Deletes every broken one (not just platform-docs)
  4. Verifies the cluster is clean

After this completes, run: task w0:migrate

Usage:
    python3 weeks/week-0/setup/recover_collection.py
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

QDRANT_URL     = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "") or None

qd = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30, check_compatibility=False)

print(f"\nConnected to: {QDRANT_URL}\n")

# ── 1. List all collections ─────────────────────────────────────────────────
try:
    cols = qd.get_collections().collections
    print(f"Collections found: {[c.name for c in cols]}\n")
except Exception as e:
    print(f"Cannot list collections: {e}")
    print("The cluster itself may be unhealthy. Check cloud.qdrant.io -> cluster status.")
    sys.exit(1)

if not cols:
    print("No collections on this cluster. Run: task w0:migrate\n")
    sys.exit(0)

# ── 2. Probe each collection — broken ones return 500 on get_collection ─────
broken: list[str] = []
healthy: list[str] = []

for c in cols:
    try:
        info = qd.get_collection(c.name)
        healthy.append(c.name)
        print(f"  ✓ {c.name:35s} {info.points_count:>8,} points  OK")
    except Exception as e:
        broken.append(c.name)
        print(f"  ✗ {c.name:35s}  BROKEN ({e.__class__.__name__})")

# ── 3. Delete every broken collection ───────────────────────────────────────
if not broken:
    print("\nNo broken collections found. Cluster is healthy.\n")
    sys.exit(0)

print(f"\nBroken collections to delete: {broken}")

for name in broken:
    try:
        qd.delete_collection(name)
        print(f"  ✓ Deleted '{name}'")
    except Exception as e:
        print(f"  ✗ Delete failed for '{name}': {e}")
        print("    Try deleting manually from the Qdrant UI: Collections -> ... -> Delete")

# ── 4. Verify cluster is clean ───────────────────────────────────────────────
try:
    remaining = [c.name for c in qd.get_collections().collections]
    print(f"\nRemaining collections: {remaining}")
    print("\nCluster is healthy. Run: task w0:migrate\n")
except Exception as e:
    print(f"\nPost-delete check failed: {e}")
    print("Wait 30s and re-check cloud.qdrant.io for cluster status.\n")
