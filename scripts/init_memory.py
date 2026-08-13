#!/usr/bin/env python3
"""
One-shot initializer for the Couchbase Enterprise Edition instance used as the
Couchbase Optimizer Agent's own memory store (short-term, episodic, long-term).
Run as a short-lived container (see the `memory-init` service in
docker-compose.yml) once `agent-memory` reports healthy.

This bucket is the agent's memory ONLY -- it is never a cluster under
management. Clusters being analyzed/optimized (Couchbase Enterprise or
Capella) are registered separately and reached over the network.

Steps:
  1. Wait for the node to accept REST calls, then initialize services/quotas.
  2. Create the `agent_memory` bucket and `agent` scope.
  3. Create three collections, one per memory tier:
       - short_term  -- rolling working context for the current analysis
                        session; documents carry a maxTTL (see SHORT_TERM_TTL_S)
                        so this tier self-prunes.
       - episodic     -- one document per discrete event: a finding raised, an
                        optimization applied, an approval decision, a chat
                        exchange. Durable, timestamped, replayable.
       - long_term    -- consolidated knowledge: per-cluster baselines, recurring
                        patterns, and lessons distilled from episodic memory
                        (see backend/app/memory/consolidation.py).
  4. Create a primary GSI index per collection (N1QL + cosine-similarity
     fallback path if vector search is momentarily unavailable).
  5. Create the three FTS vector indexes from memory-schema/*.json (requires
     Enterprise Edition or Capella). Non-fatal if this fails.

Idempotent: safe to re-run against an already-initialized cluster.
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests

HOST = os.environ.get("MEMORY_CB_HOST", "agent-memory")
USERNAME = os.environ.get("MEMORY_CB_USERNAME", "Administrator")
PASSWORD = os.environ.get("MEMORY_CB_PASSWORD", "password")
BUCKET = os.environ.get("MEMORY_CB_BUCKET", "agent_memory")
SCOPE = os.environ.get("MEMORY_CB_SCOPE", "agent")
RAM_QUOTA_MB = int(os.environ.get("MEMORY_CB_RAM_QUOTA_MB", "1024"))
INDEX_RAM_QUOTA_MB = int(os.environ.get("MEMORY_CB_INDEX_RAM_QUOTA_MB", "256"))
FTS_RAM_QUOTA_MB = int(os.environ.get("MEMORY_CB_FTS_RAM_QUOTA_MB", "256"))

# Rolling working-memory window: 6 hours. Long enough to span one continuous
# analysis pass over a cluster, short enough that short-term memory doesn't
# become a second episodic log.
SHORT_TERM_TTL_S = int(os.environ.get("MEMORY_SHORT_TERM_TTL_S", str(6 * 60 * 60)))

COLLECTIONS = ["short_term", "episodic", "long_term"]

MGMT = f"http://{HOST}:8091"
QUERY = f"http://{HOST}:8093"
FTS = f"http://{HOST}:8094"
AUTH = (USERNAME, PASSWORD)


def wait_for_node(timeout_s: int = 240) -> None:
    print(f"Waiting for {MGMT} to accept connections...")
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            r = requests.get(f"{MGMT}/pools", timeout=3)
            if r.status_code == 200:
                print("Node is reachable.")
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    print("ERROR: timed out waiting for agent-memory node.", file=sys.stderr)
    sys.exit(1)


def already_initialized() -> bool:
    r = requests.get(f"{MGMT}/pools/default", auth=AUTH, timeout=5)
    return r.status_code == 200


def init_node() -> None:
    if already_initialized():
        print("Cluster already initialized; skipping node init.")
        return

    print("Setting memory quotas...")
    requests.post(
        f"{MGMT}/pools/default",
        data={"memoryQuota": RAM_QUOTA_MB, "indexMemoryQuota": INDEX_RAM_QUOTA_MB, "ftsMemoryQuota": FTS_RAM_QUOTA_MB},
        timeout=10,
    ).raise_for_status()

    print("Enabling services: kv, index, n1ql, fts...")
    requests.post(
        f"{MGMT}/node/controller/setupServices",
        data={"services": "kv,index,n1ql,fts"},
        timeout=10,
    ).raise_for_status()

    print("Setting indexer storage mode (plasma)...")
    requests.post(f"{MGMT}/settings/indexes", data={"storageMode": "plasma"}, timeout=10).raise_for_status()

    print(f"Creating administrator user '{USERNAME}'...")
    requests.post(
        f"{MGMT}/settings/web",
        data={"username": USERNAME, "password": PASSWORD, "port": "SAME"},
        timeout=10,
    ).raise_for_status()


def ensure_bucket() -> None:
    r = requests.get(f"{MGMT}/pools/default/buckets/{BUCKET}", auth=AUTH, timeout=5)
    if r.status_code == 200:
        print(f"Bucket '{BUCKET}' already exists.")
        return
    print(f"Creating bucket '{BUCKET}'...")
    requests.post(
        f"{MGMT}/pools/default/buckets",
        auth=AUTH,
        data={
            "name": BUCKET,
            "bucketType": "couchbase",
            "ramQuotaMB": RAM_QUOTA_MB,
            "replicaNumber": 0,
            "flushEnabled": 0,
        },
        timeout=15,
    ).raise_for_status()
    time.sleep(3)


def existing_collections() -> dict:
    scopes_resp = requests.get(f"{MGMT}/pools/default/buckets/{BUCKET}/scopes", auth=AUTH, timeout=10)
    scopes_resp.raise_for_status()
    scopes = {s["name"]: s for s in scopes_resp.json().get("scopes", [])}
    if SCOPE not in scopes:
        return {}
    return {c["name"]: c for c in scopes[SCOPE].get("collections", [])}


def ensure_scope() -> None:
    scopes_resp = requests.get(f"{MGMT}/pools/default/buckets/{BUCKET}/scopes", auth=AUTH, timeout=10)
    scopes_resp.raise_for_status()
    names = [s["name"] for s in scopes_resp.json().get("scopes", [])]
    if SCOPE in names:
        return
    print(f"Creating scope '{SCOPE}'...")
    requests.post(
        f"{MGMT}/pools/default/buckets/{BUCKET}/scopes", auth=AUTH, data={"name": SCOPE}, timeout=10,
    ).raise_for_status()


def ensure_collections() -> None:
    ensure_scope()
    current = existing_collections()
    for name in COLLECTIONS:
        if name in current:
            print(f"Collection '{name}' already exists.")
            continue
        print(f"Creating collection '{name}'...")
        data = {"name": name}
        if name == "short_term":
            data["maxTTL"] = str(SHORT_TERM_TTL_S)
        requests.post(
            f"{MGMT}/pools/default/buckets/{BUCKET}/scopes/{SCOPE}/collections",
            auth=AUTH, data=data, timeout=10,
        ).raise_for_status()
        time.sleep(2)


def ensure_primary_indexes(attempts: int = 20, delay_s: float = 3.0) -> None:
    for name in COLLECTIONS:
        query = f"CREATE PRIMARY INDEX IF NOT EXISTS ON `{BUCKET}`.`{SCOPE}`.`{name}`"
        for attempt in range(1, attempts + 1):
            r = requests.post(f"{QUERY}/query/service", auth=AUTH, data={"statement": query}, timeout=30)
            if r.status_code == 200:
                print(f"Primary index ready for '{name}' (attempt {attempt}/{attempts}).")
                break
            print(f"Primary index for '{name}' not ready yet ({r.status_code}), retrying ({attempt}/{attempts})...")
            time.sleep(delay_s)
        else:
            print(f"WARNING: primary index creation for '{name}' did not succeed after {attempts} attempts.")


def ensure_vector_indexes() -> None:
    schema_dir = os.environ.get("MEMORY_SCHEMA_DIR", "/memory-schema")
    for name in COLLECTIONS:
        path = os.path.join(schema_dir, f"{name}_vector_index.json")
        with open(path) as f:
            definition = json.load(f)
        definition["sourceName"] = BUCKET
        idx_name = definition["name"]

        existing = requests.get(f"{FTS}/api/index/{idx_name}", auth=AUTH, timeout=5)
        if existing.status_code == 200:
            print(f"FTS vector index '{idx_name}' already exists.")
            continue

        print(f"Creating FTS vector index '{idx_name}' for '{name}' (requires Enterprise Edition or Capella)...")
        r = requests.put(
            f"{FTS}/api/index/{idx_name}", auth=AUTH, json=definition, timeout=20,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code not in (200, 201):
            print(f"WARNING: vector index '{idx_name}' creation returned {r.status_code}: {r.text[:400]}")
            print("Not fatal: recall() falls back to N1QL + cosine-similarity scan for this tier.")
        else:
            print(f"Vector index '{idx_name}' created.")


if __name__ == "__main__":
    wait_for_node()
    init_node()
    ensure_bucket()
    ensure_collections()
    ensure_primary_indexes()
    ensure_vector_indexes()
    print("Agent memory store initialization complete (short_term, episodic, long_term).")
