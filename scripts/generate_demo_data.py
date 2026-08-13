#!/usr/bin/env python3
"""
Seeds a real Couchbase Enterprise or Capella cluster with realistic retail
data, a deliberately imperfect index landscape, and a repeated, weighted
query workload -- so the Couchbase Optimizer Agent has actual
`system:completed_requests` history to analyze and enough real findings to
demonstrate every one of its detection rules in a demo, instead of pointing
it at an empty cluster.

This cluster is content for the agent to ANALYZE -- it is a completely
different cluster from `agent-memory` (the agent's own short-term/episodic/
long-term memory store, see docker-compose.yml). Point this script at your
own Couchbase Enterprise install or a Capella database, never at
`agent-memory` itself.

Patterns this script is designed to produce, and which rule module in
backend/app/core/rules/ each maps to:

  1.  Slow Index Times                        -- index_rules.detect_slow_index_scans
  2.  Primary Index Over-usage                 -- index_rules.detect_primary_scan_heavy
  3.  ORDER BY / LIMIT / OFFSET Over-scan       -- index_rules.detect_order_by_offset_overscan
  4.  High Kernel/CPU Time in Queries           -- query_rules.detect_high_cpu_service_time
  5.  High Memory Usage                        -- query_rules.detect_high_memory_per_query (+ resource_rules)
  6.  Slow Parse/Plan Times                    -- query_rules.detect_slow_parse_plan
  7.  Slow USE KEYS Queries                    -- query_rules.detect_slow_use_keys
  8.  Missing WHERE Clauses                    -- query_rules.detect_missing_where_clause
  9.  Complex JOIN Operations                  -- query_rules.detect_complex_joins
  10. Ineffective LIKE Operations               -- query_rules.detect_ineffective_like
  11. SELECT * Usage                           -- query_rules.detect_select_star
  12. Large Payload Streaming                  -- query_rules.detect_large_result_sets
  13. Large Result Set Queries                 -- query_rules.detect_large_result_sets
  14. Timeout-Prone Queries                    -- query_rules.detect_timeout_prone
  15. Concurrent Query Conflicts               -- query_rules.detect_concurrent_conflicts

IMPORTANT -- query monitoring must be turned all the way up first:
Couchbase's query service only logs a request into `system:completed_requests`
if it exceeds `completed-threshold` (a default of 1000ms) *or* monitoring is
set to log everything. Most of this demo's queries are fast on a small
dataset, so this script sets `completed-threshold` to 0 (log every request,
regardless of duration) via the Query Admin REST API before running the
workload -- see configure_query_monitoring() below. Without this step, the
agent's analysis pass would see almost nothing.

Usage:
    pip install -r scripts/requirements-demo.txt
    python scripts/generate_demo_data.py \\
        --connection-string couchbases://cb.xxxx.cloud.couchbase.com \\
        --username demo-admin --password '...' \\
        --register-with-agent

Run with --help for every option. Safe to re-run: seeding checks for
existing documents/indexes and skips what's already there; the query
workload can be re-run any number of times to build up more history.

If you omit --connection-string (and DEMO_CB_CONNECTION_STRING isn't set in
the environment), the script prompts for it interactively -- along with
username and password -- instead of failing; run non-interactively (e.g. in
CI) by always passing --connection-string/--password explicitly.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import logging
import os
import random
import string
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.exceptions import CouchbaseException, DocumentExistsException, TimeoutException
from couchbase.options import ClusterOptions, QueryOptions

try:
    from faker import Faker
except ImportError:  # pragma: no cover
    print("Missing dependency: pip install -r scripts/requirements-demo.txt", file=sys.stderr)
    raise

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("demo-data")
fake = Faker()

REGIONS = ["west", "east", "central", "south", "north"]
CATEGORIES = ["electronics", "home", "outdoor", "apparel", "grocery", "toys", "office"]
STATUSES = ["open", "processing", "shipped", "delivered", "cancelled"]


# --------------------------------------------------------------------------
# Connection / bucket / scope / collection setup
# --------------------------------------------------------------------------

@dataclass
class DemoConfig:
    connection_string: str
    username: str
    password: str
    bucket: str = "demo_retail"
    scope: str = "ops"
    management_url: Optional[str] = None
    skip_bucket_create: bool = False
    ram_quota_mb: int = 512
    n_customers: int = 5000
    n_products: int = 800
    n_orders: int = 15000
    n_orders_wide: int = 500
    n_sessions: int = 6000
    n_hotdocs: int = 5
    iterations: int = 8
    concurrency: int = 24
    seed: Optional[int] = None
    register_with_agent: bool = True
    agent_api: str = "http://localhost:8000"
    cluster_display_name: str = "demo-retail-cluster"


def derive_management_url(connection_string: str) -> str:
    parsed = urlparse(connection_string.replace("couchbases://", "https://").replace("couchbase://", "http://"))
    host = parsed.hostname or parsed.path
    scheme = "https" if connection_string.startswith("couchbases") else "http"
    port = 18091 if scheme == "https" else 8091
    return f"{scheme}://{host}:{port}"


def derive_query_admin_url(connection_string: str) -> str:
    parsed = urlparse(connection_string.replace("couchbases://", "https://").replace("couchbase://", "http://"))
    host = parsed.hostname or parsed.path
    scheme = "https" if connection_string.startswith("couchbases") else "http"
    port = 18093 if scheme == "https" else 8093
    return f"{scheme}://{host}:{port}"


def ensure_bucket(cfg: DemoConfig) -> None:
    if cfg.skip_bucket_create:
        log.info("Skipping bucket creation (--skip-bucket-create) -- assuming '%s' already exists (e.g. on Capella).", cfg.bucket)
        return

    mgmt = cfg.management_url or derive_management_url(cfg.connection_string)
    auth = (cfg.username, cfg.password)
    try:
        resp = requests.get(f"{mgmt}/pools/default/buckets/{cfg.bucket}", auth=auth, timeout=5)
        if resp.status_code == 200:
            log.info("Bucket '%s' already exists.", cfg.bucket)
            return
    except requests.RequestException as exc:
        log.warning(
            "Could not reach Management REST at %s (%s) -- if this is a Capella cluster, re-run with "
            "--skip-bucket-create and provision the bucket/scope from the Capella UI first.", mgmt, exc,
        )
        return

    log.info("Creating bucket '%s' (%sMB quota)...", cfg.bucket, cfg.ram_quota_mb)
    resp = requests.post(
        f"{mgmt}/pools/default/buckets", auth=auth,
        data={"name": cfg.bucket, "bucketType": "couchbase", "ramQuotaMB": cfg.ram_quota_mb, "replicaNumber": 0, "flushEnabled": 1},
        timeout=15,
    )
    if resp.status_code not in (200, 202):
        log.warning("Bucket creation returned %s: %s", resp.status_code, resp.text[:300])
    time.sleep(3)


def ensure_scope_and_collections(cluster: Cluster, cfg: DemoConfig, collections: list[str]) -> None:
    bucket = cluster.bucket(cfg.bucket)
    coll_mgr = bucket.collections()

    existing_scopes = {s.name: {c.name for c in s.collections} for s in coll_mgr.get_all_scopes()}
    if cfg.scope not in existing_scopes:
        log.info("Creating scope '%s'...", cfg.scope)
        coll_mgr.create_scope(cfg.scope)
        existing_scopes[cfg.scope] = set()

    for name in collections:
        if name in existing_scopes.get(cfg.scope, set()):
            continue
        log.info("Creating collection '%s.%s'...", cfg.scope, name)
        from couchbase.management.collections import CollectionSpec
        coll_mgr.create_collection(CollectionSpec(name, scope_name=cfg.scope))
    time.sleep(2)  # let the collection map propagate before we start writing


def configure_query_monitoring(cfg: DemoConfig) -> None:
    """Without this, most of this script's queries would be too fast to be
    retained in system:completed_requests at all -- see module docstring."""
    admin = derive_query_admin_url(cfg.connection_string)
    try:
        resp = requests.post(
            f"{admin}/admin/settings",
            auth=(cfg.username, cfg.password),
            json={"completed-threshold": 0, "completed-limit": 8000, "completed-max-plan-size": 262144},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Query monitoring configured: completed-threshold=0 (log every request), completed-limit=8000.")
        else:
            log.warning(
                "Could not set query monitoring via %s/admin/settings (%s: %s). If findings look sparse "
                "after running, ask a cluster admin to set completed-threshold to 0 manually (Query "
                "Settings in the UI, or the Query Admin REST API), then re-run this script.",
                admin, resp.status_code, resp.text[:200],
            )
    except requests.RequestException as exc:
        log.warning("Could not reach Query Admin REST at %s (%s) -- same fallback as above applies.", admin, exc)


# --------------------------------------------------------------------------
# Index landscape -- deliberately imperfect, matching what index_rules looks for
# --------------------------------------------------------------------------

def create_indexes(cluster: Cluster, cfg: DemoConfig) -> None:
    ks = lambda name: f"`{cfg.bucket}`.`{cfg.scope}`.`{name}`"  # noqa: E731
    statements = [
        # customers: ONLY a primary index -- email/region/tier are never
        # indexed, so filtering on them forces a primary scan
        # (index_rules.detect_primary_scan_heavy).
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {ks('customers')}",

        # orders: indexed on customer_id (used, healthy) but NOT on
        # created_at or status -- so ORDER BY created_at (over-scan) and
        # status filters (primary-scan-adjacent) both stay unaddressed.
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {ks('orders')}",
        f"CREATE INDEX IF NOT EXISTS idx_orders_customer ON {ks('orders')}(customer_id) WITH {{\"num_replica\": 1}}",

        # products: category index deliberately created WITHOUT a replica
        # (index_rules.detect_missing_index_replicas), and with a wide/low-
        # selectivity leading key that produces a slow index-scan phase when
        # combined with a price range predicate that isn't part of the key
        # (index_rules.detect_slow_index_scans).
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {ks('products')}",
        f"CREATE INDEX IF NOT EXISTS idx_products_category ON {ks('products')}(category) WITH {{\"num_replica\": 0}}",

        # sessions: an index that the workload below never actually drives a
        # query through (queries hit sessions with no WHERE, or filter on a
        # different field) -- index_rules.detect_unused_indexes.
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {ks('sessions')}",
        f"CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON {ks('sessions')}(user_id) WITH {{\"num_replica\": 1}}",

        # orders_wide / hotdocs: primary indexes only, so USE KEYS / hot-key
        # writes work; no secondary indexes needed for how the workload uses them.
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {ks('orders_wide')}",
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {ks('hotdocs')}",
    ]
    for stmt in statements:
        try:
            cluster.query(stmt).execute()
            log.info("OK  %s", stmt.split(" WITH")[0])
        except CouchbaseException as exc:
            log.warning("Index statement failed (continuing): %s -- %s", stmt[:80], exc)
    log.info("Waiting for indexes to come online...")
    time.sleep(8)


# --------------------------------------------------------------------------
# Data seeding
# --------------------------------------------------------------------------

def _padded_blob(kb: int) -> str:
    return "".join(random.choices(string.ascii_letters, k=kb * 1024))


def _phone_like(n: int) -> str:
    return f"{random.randint(100,999)}-{random.randint(1000,9999)}" if n % 5 == 0 else fake.sentence(nb_words=8)


def seed_collection(cluster: Cluster, cfg: DemoConfig, name: str, count: int, doc_fn: Callable[[int], tuple[str, dict]], workers: int = 16) -> list[str]:
    collection = cluster.bucket(cfg.bucket).scope(cfg.scope).collection(name)
    keys: list[str] = []

    def _write(i: int) -> str:
        key, doc = doc_fn(i)
        try:
            collection.upsert(key, doc)
        except DocumentExistsException:
            pass
        return key

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for key in pool.map(_write, range(count)):
            keys.append(key)
    log.info("Seeded %d documents into '%s'.", len(keys), name)
    return keys


def seed_all(cluster: Cluster, cfg: DemoConfig) -> dict[str, list[str]]:
    if cfg.seed is not None:
        random.seed(cfg.seed)
        Faker.seed(cfg.seed)

    def customer_doc(i: int) -> tuple[str, dict]:
        key = f"cust::{i}"
        return key, {
            "type": "customer",
            "name": fake.name(),
            "email": fake.email(),
            "region": random.choice(REGIONS),
            "tier": random.randint(1, 5),
            "notes": _phone_like(i),
            "created_at": fake.date_time_between(start_date="-2y").isoformat(),
        }

    def product_doc(i: int) -> tuple[str, dict]:
        key = f"prod::{i}"
        return key, {
            "type": "product",
            "name": fake.catch_phrase(),
            "category": random.choice(CATEGORIES),
            "price": round(random.uniform(5, 500), 2),
        }

    customer_keys = seed_collection(cluster, cfg, "customers", cfg.n_customers, customer_doc)
    product_keys = seed_collection(cluster, cfg, "products", cfg.n_products, product_doc)

    def order_doc(i: int) -> tuple[str, dict]:
        key = f"order::{i}"
        return key, {
            "type": "order",
            "customer_id": random.choice(customer_keys),
            "product_id": random.choice(product_keys),
            "status": random.choice(STATUSES),
            "total": round(random.uniform(10, 2000), 2),
            "created_at": fake.date_time_between(start_date="-1y").isoformat(),
        }

    order_keys = seed_collection(cluster, cfg, "orders", cfg.n_orders, order_doc)

    def order_wide_doc(i: int) -> tuple[str, dict]:
        key = f"orderw::{i}"
        line_items = [
            {"sku": f"SKU-{random.randint(1000,9999)}", "price": round(random.uniform(5, 300), 2), "qty": random.randint(1, 12)}
            for _ in range(random.randint(120, 300))
        ]
        return key, {
            "type": "order_wide",
            "customer_id": random.choice(customer_keys),
            "status": random.choice(STATUSES),
            "line_items": line_items,
            "notes_blob": _padded_blob(random.randint(60, 220)),  # 60-220KB per document
            "created_at": fake.date_time_between(start_date="-1y").isoformat(),
        }

    order_wide_keys = seed_collection(cluster, cfg, "orders_wide", cfg.n_orders_wide, order_wide_doc)

    def session_doc(i: int) -> tuple[str, dict]:
        key = f"sess::{i}"
        return key, {
            "type": "session",
            "user_id": random.choice(customer_keys),
            "active": random.random() < 0.7,
            "last_seen": fake.date_time_between(start_date="-30d").isoformat(),
            "event_log": [fake.word() for _ in range(random.randint(5, 40))],
        }

    session_keys = seed_collection(cluster, cfg, "sessions", cfg.n_sessions, session_doc)

    def hotdoc(i: int) -> tuple[str, dict]:
        return f"hot::{i}", {"type": "hotdoc", "status": "idle", "counter": 0}

    hotdoc_keys = seed_collection(cluster, cfg, "hotdocs", cfg.n_hotdocs, hotdoc, workers=4)

    return {
        "customers": customer_keys, "products": product_keys, "orders": order_keys,
        "orders_wide": order_wide_keys, "sessions": session_keys, "hotdocs": hotdoc_keys,
    }


# --------------------------------------------------------------------------
# Query workload -- one entry per pattern in the module docstring
# --------------------------------------------------------------------------

@dataclass
class QueryPattern:
    name: str
    build: Callable[[dict[str, list[str]]], str]
    weight: int = 1
    timeout_s: Optional[float] = None
    concurrency_burst: int = 1  # >1 fires this many copies at once via the thread pool


def build_patterns(cfg: DemoConfig) -> list[QueryPattern]:
    b = lambda name: f"`{cfg.bucket}`.`{cfg.scope}`.`{name}`"  # noqa: E731

    def p_slow_index_scan(keys):
        cat = random.choice(CATEGORIES)
        return f"SELECT p.name, p.price FROM {b('products')} p WHERE p.category = \"{cat}\" AND p.price > {random.randint(10, 400)}"

    def p_primary_scan(keys):
        # No index on email -- forces a primary scan of every customer.
        return f"SELECT c.name, c.tier FROM {b('customers')} c WHERE c.email = \"{fake.email()}\""

    def p_order_by_offset(keys):
        offset = random.choice([800, 1500, 3000, 6000])
        return f"SELECT META(o).id AS order_id, o.status, o.total FROM {b('orders')} o ORDER BY o.created_at DESC LIMIT 20 OFFSET {offset}"

    def p_cpu_heavy_regexp(keys):
        return f"SELECT c.name FROM {b('customers')} c WHERE REGEXP_CONTAINS(c.notes, \"[0-9]{{3}}-[0-9]{{4}}\")"

    def p_cpu_heavy_array(keys):
        key = random.choice(keys["orders_wide"])
        return f"SELECT ow.customer_id, (ARRAY li.price * li.qty FOR li IN ow.line_items END) AS totals FROM {b('orders_wide')} ow USE KEYS \"{key}\""

    def p_high_memory_group_by(keys):
        return f"SELECT o.customer_id, COUNT(*) AS n, SUM(o.total) AS revenue FROM {b('orders')} o GROUP BY o.customer_id ORDER BY revenue DESC"

    def p_slow_parse_plan(keys):
        ids = ", ".join(f"\"{k}\"" for k in random.sample(keys["orders"], k=min(350, len(keys["orders"]))))
        return f"SELECT o.status FROM {b('orders')} o WHERE META(o).id IN [{ids}]"

    def p_slow_use_keys(keys):
        sample = random.sample(keys["orders_wide"], k=min(120, len(keys["orders_wide"])))
        key_list = ", ".join(f"\"{k}\"" for k in sample)
        return f"SELECT * FROM {b('orders_wide')} USE KEYS [{key_list}]"

    def p_missing_where(keys):
        return f"SELECT * FROM {b('sessions')}"

    def p_complex_join(keys):
        return (
            f"SELECT o.status, c.name, p.name AS product_name "
            f"FROM {b('orders')} o "
            f"JOIN {b('customers')} c ON KEYS o.customer_id "
            f"JOIN {b('products')} p ON KEYS o.product_id"
        )

    def p_ineffective_like(keys):
        fragment = random.choice(["son", "ing", "an", "er", "el"])
        return f"SELECT c.name, c.email FROM {b('customers')} c WHERE c.name LIKE \"%{fragment}%\""

    def p_select_star_status(keys):
        status = random.choice(STATUSES)
        return f"SELECT * FROM {b('orders')} WHERE status = \"{status}\""

    def p_large_payload(keys):
        status = random.choice(STATUSES)
        return f"SELECT * FROM {b('orders_wide')} WHERE status = \"{status}\""

    def p_large_result_set(keys):
        return f"SELECT * FROM {b('sessions')} WHERE active = true"

    def p_timeout_prone(keys):
        # No supporting index for this predicate + a client-side timeout
        # short enough that a full primary/table scan won't finish in time.
        return f"SELECT c.name FROM {b('customers')} c WHERE LOWER(c.notes) LIKE \"%{fake.word()}%\" AND c.tier > 0"

    def p_hot_write(keys):
        key = random.choice(keys["hotdocs"])
        return f"UPDATE {b('hotdocs')} SET status = \"busy\", counter = counter + 1 WHERE META().id = \"{key}\""

    return [
        QueryPattern("slow_index_scan", p_slow_index_scan, weight=6),
        QueryPattern("primary_scan_heavy", p_primary_scan, weight=10),
        QueryPattern("order_by_offset_overscan", p_order_by_offset, weight=5),
        QueryPattern("cpu_heavy_regexp", p_cpu_heavy_regexp, weight=6),
        QueryPattern("cpu_heavy_array", p_cpu_heavy_array, weight=5),
        QueryPattern("high_memory_group_by", p_high_memory_group_by, weight=4),
        QueryPattern("slow_parse_plan", p_slow_parse_plan, weight=4),
        QueryPattern("slow_use_keys", p_slow_use_keys, weight=4),
        QueryPattern("missing_where", p_missing_where, weight=5),
        QueryPattern("complex_join", p_complex_join, weight=5),
        QueryPattern("ineffective_like", p_ineffective_like, weight=6),
        QueryPattern("select_star_status", p_select_star_status, weight=8),
        QueryPattern("large_payload_streaming", p_large_payload, weight=5),
        QueryPattern("large_result_set", p_large_result_set, weight=4),
        QueryPattern("timeout_prone", p_timeout_prone, weight=5, timeout_s=1.5),
        QueryPattern("concurrent_hot_writes", p_hot_write, weight=6, concurrency_burst=12),
    ]


def _run_one(cluster: Cluster, statement: str, timeout_s: Optional[float]) -> tuple[bool, str]:
    try:
        opts = QueryOptions(timeout=timedelta(seconds=timeout_s)) if timeout_s else QueryOptions()
        list(cluster.query(statement, opts).execute())
        return True, ""
    except TimeoutException:
        return False, "timeout"
    except CouchbaseException as exc:
        return False, str(exc)[:150]


def run_workload(cluster: Cluster, cfg: DemoConfig, keys: dict[str, list[str]]) -> None:
    patterns = build_patterns(cfg)
    weighted: list[QueryPattern] = []
    for p in patterns:
        weighted.extend([p] * p.weight)

    tally: dict[str, dict[str, int]] = {p.name: {"ok": 0, "error": 0, "timeout": 0} for p in patterns}

    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
        for iteration in range(1, cfg.iterations + 1):
            log.info("Workload iteration %d/%d...", iteration, cfg.iterations)
            random.shuffle(weighted)
            futures = []
            for pattern in weighted:
                statement = pattern.build(keys)
                copies = pattern.concurrency_burst
                for _ in range(copies):
                    futures.append((pattern, pool.submit(_run_one, cluster, statement, pattern.timeout_s)))

            for pattern, future in futures:
                ok, detail = future.result()
                if ok:
                    tally[pattern.name]["ok"] += 1
                elif detail == "timeout":
                    tally[pattern.name]["timeout"] += 1
                else:
                    tally[pattern.name]["error"] += 1

    log.info("Workload complete. Per-pattern results:")
    for name, counts in tally.items():
        log.info("  %-26s ok=%-5d timeout=%-4d error=%-4d", name, counts["ok"], counts["timeout"], counts["error"])


# --------------------------------------------------------------------------
# Optional: register this cluster with a running Optimizer Agent backend
# --------------------------------------------------------------------------

def register_with_agent(cfg: DemoConfig) -> None:
    payload = {
        "name": cfg.cluster_display_name,
        "kind": "capella" if "cloud.couchbase.com" in cfg.connection_string else "enterprise",
        "connection_string": cfg.connection_string,
        "username": cfg.username,
        "password": cfg.password,
    }
    try:
        resp = requests.post(f"{cfg.agent_api}/api/clusters", json=payload, timeout=15)
        resp.raise_for_status()
        cluster_id = resp.json()["cluster_id"]
        log.info("Registered '%s' with the agent (cluster_id=%s).", cfg.cluster_display_name, cluster_id)
    except requests.RequestException as exc:
        log.warning(
            "Could not register with the agent at %s (%s). Register manually from the Clusters page, or "
            "start the stack with `docker compose up` first.", cfg.agent_api, exc,
        )
        return

    log.info("Triggering an analysis pass...")
    try:
        resp = requests.post(f"{cfg.agent_api}/api/analysis/{cluster_id}/run", timeout=120)
        resp.raise_for_status()
        summary = resp.json()
        log.info(
            "Analysis complete: %d findings created, %d updated, from %d queries and %d indexes examined.",
            summary["findings_created"], summary["findings_updated"],
            summary["queries_examined"], summary["indexes_examined"],
        )
        log.info("Open the UI and check Insights: http://localhost:5173/insights")
    except requests.RequestException as exc:
        log.warning("Analysis trigger failed (%s) -- it will still run automatically on the next scheduled pass.", exc)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _prompt(text: str, default: Optional[str] = None) -> str:
    """input() with an optional default shown/returned on an empty response,
    and a re-ask loop if there's no default and the user just hits enter."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{text}{suffix}: ").strip()
        except EOFError:
            value = ""
        if value:
            return value
        if default is not None:
            return default
        print("  (required -- please enter a value)")


def _prompt_password(text: str = "Password") -> str:
    """getpass() so the password isn't echoed to the terminal or left in
    shell history the way a --password flag would be."""
    while True:
        try:
            value = getpass.getpass(f"{text}: ")
        except EOFError:
            value = ""
        if value:
            return value
        print("  (required -- please enter a value)")


def _interactive_connection_prompt() -> tuple[str, str, str, bool]:
    """Walks through the handful of inputs actually needed to connect and
    import data, for anyone running this without CLI flags -- returns
    (connection_string, username, password, skip_bucket_create)."""
    print("No --connection-string supplied -- let's collect what's needed to connect.")
    print("(Run with --connection-string/--username/--password to skip this next time.)\n")

    kind = _prompt("Couchbase Capella or self-hosted Enterprise? [capella/enterprise]", default="enterprise").strip().lower()
    is_capella = kind.startswith("cap")

    if is_capella:
        connection_string = _prompt("Capella connection string (couchbases://cb.xxxx.cloud.couchbase.com)")
    else:
        connection_string = _prompt("Connection string", default="couchbase://localhost")

    username = _prompt("Username", default="Administrator")
    password = _prompt_password("Password")

    skip_bucket_create = False
    if is_capella:
        print(
            "\nCapella doesn't allow this script to create buckets over the Management REST API -- "
            "assuming --skip-bucket-create. Provision the 'demo_retail' bucket with an 'ops' scope "
            "from the Capella UI first if you haven't already.\n"
        )
        skip_bucket_create = True

    return connection_string, username, password, skip_bucket_create


def parse_args(argv: list[str]) -> DemoConfig:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--connection-string", default=os.environ.get("DEMO_CB_CONNECTION_STRING"),
                    help="e.g. couchbase://localhost or couchbases://cb.xxxx.cloud.couchbase.com. "
                         "Omit to be prompted interactively.")
    p.add_argument("--username", default=os.environ.get("DEMO_CB_USERNAME", "Administrator"))
    p.add_argument("--password", default=os.environ.get("DEMO_CB_PASSWORD", ""))
    p.add_argument("--bucket", default="demo_retail")
    p.add_argument("--scope", default="ops")
    p.add_argument("--management-url", default=None, help="Override auto-derived Management REST URL.")
    p.add_argument("--skip-bucket-create", action="store_true", help="Use on Capella -- provision the bucket from the Capella UI first.")
    p.add_argument("--ram-quota-mb", type=int, default=512)
    p.add_argument("--customers", type=int, default=5000)
    p.add_argument("--products", type=int, default=800)
    p.add_argument("--orders", type=int, default=15000)
    p.add_argument("--orders-wide", type=int, default=500)
    p.add_argument("--sessions", type=int, default=6000)
    p.add_argument("--iterations", type=int, default=8, help="How many times to run through the full weighted query workload.")
    p.add_argument("--concurrency", type=int, default=24, help="Thread pool size for query execution / concurrent write bursts.")
    p.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible demo data.")
    p.add_argument("--skip-seed", action="store_true", help="Skip data/index seeding and just run the query workload again.")
    p.add_argument("--register-with-agent", dest="register", action="store_true", default=True)
    p.add_argument("--no-register-with-agent", dest="register", action="store_false")
    p.add_argument("--agent-api", default=os.environ.get("AGENT_API_URL", "http://localhost:8000"))
    p.add_argument("--cluster-name", default="demo-retail-cluster")
    args = p.parse_args(argv)

    connection_string = args.connection_string
    username = args.username
    password = args.password
    skip_bucket_create = args.skip_bucket_create

    if not connection_string:
        if not sys.stdin.isatty():
            p.error(
                "--connection-string is required (or set DEMO_CB_CONNECTION_STRING) when stdin isn't "
                "a terminal -- interactive prompting needs an interactive terminal."
            )
        connection_string, username, password, prompted_skip_bucket_create = _interactive_connection_prompt()
        skip_bucket_create = skip_bucket_create or prompted_skip_bucket_create
    elif not password:
        if not sys.stdin.isatty():
            p.error("--password is required (or set DEMO_CB_PASSWORD) when stdin isn't a terminal.")
        password = _prompt_password(f"Password for '{username}'")

    return DemoConfig(
        connection_string=connection_string, username=username, password=password,
        bucket=args.bucket, scope=args.scope, management_url=args.management_url,
        skip_bucket_create=skip_bucket_create, ram_quota_mb=args.ram_quota_mb,
        n_customers=args.customers, n_products=args.products, n_orders=args.orders,
        n_orders_wide=args.orders_wide, n_sessions=args.sessions,
        iterations=args.iterations, concurrency=args.concurrency, seed=args.seed,
        register_with_agent=args.register, agent_api=args.agent_api, cluster_display_name=args.cluster_name,
    ), args.skip_seed


def main(argv: list[str]) -> int:
    cfg, skip_seed = parse_args(argv)

    log.info("Connecting to %s ...", cfg.connection_string)
    auth = PasswordAuthenticator(cfg.username, cfg.password)
    cluster = Cluster(cfg.connection_string, ClusterOptions(auth))
    cluster.wait_until_ready(timeout=timedelta(seconds=20))

    collections = ["customers", "products", "orders", "orders_wide", "sessions", "hotdocs"]

    if not skip_seed:
        ensure_bucket(cfg)
        ensure_scope_and_collections(cluster, cfg, collections)
        configure_query_monitoring(cfg)
        create_indexes(cluster, cfg)
        keys = seed_all(cluster, cfg)
    else:
        log.info("--skip-seed set: reusing existing data, re-deriving key lists via N1QL...")
        configure_query_monitoring(cfg)
        keys = {}
        for name in collections:
            rows = cluster.query(
                f"SELECT RAW META(d).id FROM `{cfg.bucket}`.`{cfg.scope}`.`{name}` d LIMIT 20000"
            ).execute()
            keys[name] = list(rows)
            log.info("Found %d existing keys in '%s'.", len(keys[name]), name)

    log.info("Running query workload (%d iterations, concurrency=%d)...", cfg.iterations, cfg.concurrency)
    run_workload(cluster, cfg, keys)

    cluster.close()

    if cfg.register_with_agent:
        register_with_agent(cfg)
    else:
        log.info(
            "Done. Register '%s' (%s) with the agent from the Clusters page, or POST /api/clusters, then "
            "run an analysis pass to see findings.", cfg.cluster_display_name, cfg.connection_string,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
