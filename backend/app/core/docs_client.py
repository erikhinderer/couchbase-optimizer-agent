"""
Pulls reference material from the Couchbase documentation website (and, if
ever needed, other allow-listed sites) so every recommendation the agent
makes can show a source link underneath it for validation.

docs.couchbase.com publishes a markdown twin of every page (swap the .html
extension for .md) plus a site-wide index at /llms.txt -- this client prefers
the markdown twin when fetching a known page (cleaner input for the LLM/RAG
prompt than scraping rendered HTML) and falls back to a light HTML-tag strip
if markdown isn't available for a given URL.

CURATED_TOPICS is a small, hand-verified seed catalog mapping the finding
topics the rule engine actually produces to real docs.couchbase.com pages, so
citations are correct even before/without a live fetch succeeding. Every
fetch is still restricted to settings.allowed_doc_domain_list regardless of
whether the URL came from the seed catalog or was looked up dynamically.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.models.schemas import DocReference

logger = logging.getLogger(__name__)

CURATED_TOPICS: dict[str, list[DocReference]] = {
    "primary_index_scan": [
        DocReference(
            title="CREATE INDEX",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/createindex.html",
        ),
        DocReference(
            title="Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html#index-selection",
        ),
    ],
    "unused_index": [
        DocReference(
            title="DROP INDEX",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/dropindex.html",
        ),
        DocReference(
            title="system:indexes / system:completed_requests catalogs",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
    ],
    "index_replica_missing": [
        DocReference(
            title="Index Replication",
            url="https://docs.couchbase.com/server/current/indexes/index-replication.html#index-replication",
        ),
        DocReference(
            title="ALTER INDEX",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/alterindex.html",
        ),
    ],
    "bucket_memory_pressure": [
        DocReference(
            title="Memory -- Service and Bucket Memory Quotas",
            url="https://docs.couchbase.com/server/current/learn/buckets-memory-and-storage/memory.html",
        ),
        DocReference(
            title="Memory Watermarks",
            url="https://docs.couchbase.com/server/current/learn/buckets-memory-and-storage/memory.html#watermarks",
        ),
    ],
    "large_result_set": [
        DocReference(
            title="SELECT / Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html",
        ),
    ],
    "select_star": [
        DocReference(
            title="SELECT / Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html",
        ),
    ],
    "query_monitoring": [
        DocReference(
            title="system:indexes / system:completed_requests catalogs",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
    ],
    "slow_index_scan": [
        DocReference(
            title="CREATE INDEX",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/createindex.html",
        ),
        DocReference(
            title="system:completed_requests -- phaseTimes/phaseCounts",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
    ],
    "order_by_offset_overscan": [
        DocReference(
            title="SELECT / Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html",
        ),
    ],
    "high_memory_per_query": [
        DocReference(
            title="Memory -- Service and Bucket Memory Quotas",
            url="https://docs.couchbase.com/server/current/learn/buckets-memory-and-storage/memory.html",
        ),
        DocReference(
            title="system:completed_requests -- usedMemory",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
    ],
    "high_cpu_service_time": [
        DocReference(
            title="system:completed_requests -- phaseTimes/serviceTime",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
        DocReference(
            title="String Functions (UPPER/LOWER/REGEXP_*)",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/stringfun.html",
        ),
    ],
    "slow_parse_plan": [
        DocReference(
            title="system:completed_requests -- phaseTimes",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
        DocReference(
            title="SELECT / Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html",
        ),
    ],
    "slow_use_keys": [
        DocReference(
            title="SELECT / Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html",
        ),
    ],
    "missing_where_clause": [
        DocReference(
            title="SELECT / Index Selection",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/selectintro.html",
        ),
    ],
    "complex_join": [
        DocReference(
            title="JOIN Clause",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/join.html",
        ),
    ],
    "ineffective_like": [
        DocReference(
            title="Comparison Operators -- LIKE",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/comparisonops.html#like",
        ),
    ],
    "timeout_prone": [
        DocReference(
            title="system:completed_requests / system:active_requests catalogs",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
    ],
    "concurrent_conflicts": [
        DocReference(
            title="system:completed_requests / system:active_requests catalogs",
            url="https://docs.couchbase.com/server/current/n1ql/n1ql-intro/sysinfo.html",
        ),
        DocReference(
            title="Memory -- Service and Bucket Memory Quotas",
            url="https://docs.couchbase.com/server/current/learn/buckets-memory-and-storage/memory.html",
        ),
    ],
}

_TAG_RE = re.compile(r"<[^>]+>")


def _cache_path(cache_dir: str, url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return os.path.join(cache_dir, f"{digest}.txt")


def _domain_allowed(url: str) -> bool:
    settings = get_settings()
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith(f".{d}") for d in settings.allowed_doc_domain_list)


def references_for_topic(topic: str) -> list[DocReference]:
    return list(CURATED_TOPICS.get(topic, []))


async def fetch_snippet(doc: DocReference, max_chars: int = 600) -> DocReference:
    """Best-effort: fetch the page (preferring its markdown twin), cache it,
    and attach a short snippet to the DocReference for citation context. Never
    raises -- a citation with no snippet is still a valid, clickable source
    link, which is all validation strictly requires."""
    settings = get_settings()
    if not _domain_allowed(doc.url):
        logger.warning("Refusing to fetch doc outside allow-listed domains: %s", doc.url)
        return doc

    os.makedirs(settings.docs_cache_dir, exist_ok=True)
    cache_file = _cache_path(settings.docs_cache_dir, doc.url)
    if os.path.exists(cache_file) and (time.time() - os.path.getmtime(cache_file)) < settings.docs_cache_ttl_s:
        text = open(cache_file).read()
        doc.snippet = text[:max_chars]
        return doc

    md_url = doc.url[:-5] + ".md" if doc.url.endswith(".html") else None
    text = None
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for candidate in filter(None, [md_url, doc.url]):
            try:
                resp = await client.get(candidate, headers={"User-Agent": "couchbase-optimizer-agent/1.0"})
                if resp.status_code == 200 and resp.text:
                    text = resp.text if candidate == md_url else _TAG_RE.sub(" ", resp.text)
                    break
            except httpx.HTTPError as exc:
                logger.info("Doc fetch failed for %s: %s", candidate, exc)

    if text:
        cleaned = re.sub(r"\s+", " ", text).strip()
        with open(cache_file, "w") as f:
            f.write(cleaned)
        doc.snippet = cleaned[:max_chars]
    return doc


async def enrich_references(topic: str) -> list[DocReference]:
    docs = references_for_topic(topic)
    enriched = []
    for d in docs:
        enriched.append(await fetch_snippet(d))
    return enriched
