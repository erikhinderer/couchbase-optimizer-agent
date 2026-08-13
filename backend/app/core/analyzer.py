"""Orchestrates one analysis pass over a registered cluster: pull stats,
run every rule module, test SAFE_AUTO index findings in the WASM sandbox,
attach documentation citations, dedupe against already-open findings, and
persist the result -- writing an episodic memory record either way so the
agent's history reflects every pass, not just the ones that found something.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable
from uuid import UUID

import httpx

from app.config import get_settings
from app.core.bundle_client import BundleClusterClient
from app.core.cluster_client import ClusterClient
from app.core.docs_client import enrich_references
from app.core.query_rewriter import suggest_optimized_query
from app.core.rules import index_rules, query_rules, resource_rules
from app.core.rules.base import ClusterStats
from app.core.sandbox_client import SandboxClient
from app.core.store import StateStore
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import ActionType, ClusterSourceType, FindingCategory, FindingStatus, MemoryTier
from app.models.schemas import AnalysisRunSummary, Cluster, Finding, SandboxTestResult

logger = logging.getLogger(__name__)

RULE_MODULES = [index_rules, query_rules, resource_rules]

ProgressCallback = Callable[[str, dict], None] | None

# ClusterClient and BundleClusterClient are deliberately duck-typed to the
# same interface (completed_requests/index_catalog/bucket_names/
# node_and_bucket_stats/close) so gather_stats() below doesn't need to know
# which kind of cluster it's looking at.
AnyClusterClient = ClusterClient | BundleClusterClient

# asyncio only holds a weak reference to a bare create_task() result -- with
# nothing else referencing it, the task can be garbage-collected mid-flight.
# Keeping a strong reference here until each one finishes avoids background
# query-rewrite drafts silently vanishing under load.
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _make_client(cluster: Cluster) -> AnyClusterClient:
    if cluster.source_type == ClusterSourceType.SUPPORT_BUNDLE:
        return BundleClusterClient(cluster)
    return ClusterClient(cluster)


def _emit_activity(on_progress: ProgressCallback, cluster: Cluster, state: str, message: str) -> None:
    """Broadcasts a coarse-grained 'what is the agent doing right now' signal
    -- consumed by the sidebar status indicator so it reflects real activity
    (analyzing / testing_sandbox / validating / idle) instead of a static
    'online' label. Best-effort: a dropped event just means the UI is a beat
    behind, never a reason to fail the analysis pass itself."""
    if not on_progress:
        return
    on_progress("agent_activity", {"cluster_id": str(cluster.cluster_id), "state": state, "message": message})


def _topic_for_finding(finding: Finding) -> str:
    title = finding.title.lower()
    if "no replica" in title:
        return "index_replica_missing"
    if "no observed usage" in title:
        return "unused_index"
    if "primary index scan" in title:
        return "primary_index_scan"
    if "slow index-scan" in title:
        return "slow_index_scan"
    if "offset" in title and "over-scan" in title:
        return "order_by_offset_overscan"
    if "memory quota" in title:
        return "bucket_memory_pressure"
    if "query-engine memory" in title:
        return "high_memory_per_query"
    if "cpu time" in title:
        return "high_cpu_service_time"
    if "parse/plan" in title:
        return "slow_parse_plan"
    if "use keys" in title:
        return "slow_use_keys"
    if "no where clause" in title:
        return "missing_where_clause"
    if "joins" in title and "keyspaces" in title:
        return "complex_join"
    if "leading wildcard" in title:
        return "ineffective_like"
    if "times out or errors" in title:
        return "timeout_prone"
    if "concurrent write conflicts" in title:
        return "concurrent_conflicts"
    if "select *" in title or "entire documents" in title:
        return "select_star"
    if "large result" in title or "large payloads" in title:
        return "large_result_set"
    return "query_monitoring"


def _is_system_catalog_query(row: dict) -> bool:
    return "system:" in (row.get("statement") or "").lower()


async def gather_stats(client: AnyClusterClient, lookback: int) -> ClusterStats:
    # ClusterClient.completed_requests() already excludes these at the SQL
    # level (see its WHERE clause) -- this is defense-in-depth so the same
    # guarantee holds for BundleClusterClient (support-bundle snapshots),
    # which just returns whatever a cbcollect_info archive captured verbatim
    # and can't apply that filter itself.
    completed_requests = [r for r in client.completed_requests(lookback) if not _is_system_catalog_query(r)]
    return ClusterStats(
        completed_requests=completed_requests,
        index_catalog=client.index_catalog(),
        resource_stats=await client.node_and_bucket_stats(),
        bucket_names=client.bucket_names(),
    )


async def _sandbox_test(finding: Finding, stats: ClusterStats) -> SandboxTestResult | None:
    """Only index-creation findings have enough shape (doc_count, an
    estimated selectivity) to run through the cost-model simulation; other
    SAFE_AUTO kinds (replica count, RAM quota) are structurally safe
    metadata/config changes rather than plan changes, so there's nothing
    useful for the sandbox to simulate."""
    if not finding.suggested_action or finding.category != FindingCategory.INDEX:
        return None
    if "CREATE INDEX" not in (finding.suggested_action.get("statement") or ""):
        return None

    evidence = finding.evidence or {}
    occurrence_count = evidence.get("occurrence_count", 1)
    sample = next(
        (r for r in stats.completed_requests if r.get("statement") == evidence.get("sample_statement")), None
    )
    result_count = (sample or {}).get("resultCount") or 1
    doc_count = max(occurrence_count * 5000, 50_000)  # conservative stand-in when true bucket count is unknown
    selectivity = max(1, min(1000, int(1000 * result_count / max(doc_count, 1))))

    try:
        sim = await SandboxClient().simulate_query_plan_cost(
            doc_count=doc_count,
            avg_doc_size_bytes=1024,
            before_uses_index=False,
            before_selectivity_permille=1000,
            after_uses_index=True,
            after_selectivity_permille=selectivity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sandbox simulation failed for finding %s: %s", finding.finding_id, exc)
        return None

    return SandboxTestResult(
        passed=sim["passed"],
        summary=(
            f"Sandboxed cost-model check: estimated scan cost drops {sim['improvement_pct']}% "
            f"({sim['before_cost_kb']}KB -> {sim['after_cost_kb']}KB) after indexing."
        ),
        detail=sim,
        fuel_consumed=sim.get("fuel_consumed"),
    )


async def _draft_query_suggestion_background(
    finding_id: UUID, store: StateStore, on_progress: ProgressCallback
) -> None:
    """Runs the local-LLM query rewrite after the finding has already been
    saved and broadcast, instead of inline in the main analysis loop. A
    cluster with a dozen REQUIRES_CODE_CHANGE findings in one pass was
    turning every pass into a dozen sequential LLM completions -- multiple
    minutes blocking finding visibility, the 'run now' HTTP response (and the
    dashboard navigation hanging off it), and the next scheduled pass. None
    of that needs to wait on this; it's an optional annotation, not part of
    what makes the finding itself useful."""
    current = await store.get_finding(finding_id)
    if not current:
        return
    try:
        suggestion = await suggest_optimized_query(current)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Background query rewrite failed for finding %s: %s", finding_id, exc)
        return
    if not suggestion:
        return

    # Re-fetch rather than reuse `current` -- the finding may have been
    # approved/rejected/updated while the LLM call was in flight.
    latest = await store.get_finding(finding_id)
    if not latest:
        return
    latest.suggested_query = suggestion
    await store.save_finding(latest)
    if on_progress:
        on_progress(
            "finding_updated",
            {"cluster_id": str(latest.cluster_id), "finding": latest.model_dump(mode="json")},
        )


async def run_analysis(cluster: Cluster, on_progress: ProgressCallback = None) -> AnalysisRunSummary:
    settings = get_settings()
    started = datetime.utcnow()
    # Use the shared singleton, not a bare StateStore() -- a fresh instance
    # loads its own in-memory copy of state.json and, while its own writes do
    # get persisted to disk correctly, every API route reads through
    # StateStore.instance(), which never re-reads that file after startup.
    # A disconnected instance here meant findings written during analysis
    # were durably saved yet invisible to every GET /api/findings call.
    store = StateStore.instance()
    memory = AgentMemoryStore.instance()
    client = _make_client(cluster)

    _emit_activity(on_progress, cluster, "analyzing", f"Analyzing {cluster.name} -- pulling query and index stats")
    try:
        stats = await gather_stats(client, settings.completed_requests_lookback)
    finally:
        client.close()

    _emit_activity(on_progress, cluster, "analyzing", f"Analyzing {cluster.name} -- running rule engine")
    raw_findings: list[Finding] = []
    for module in RULE_MODULES:
        raw_findings.extend(module.detect(cluster.cluster_id, stats))

    created, updated = 0, 0
    for finding in raw_findings:
        existing = await store.find_open_duplicate(cluster.cluster_id, finding.title)
        if existing:
            existing.evidence = finding.evidence
            existing.detected_at = datetime.utcnow()
            await store.save_finding(existing)
            updated += 1
            continue

        _emit_activity(on_progress, cluster, "validating", f"Validating '{finding.title}' against Couchbase documentation")
        topic = _topic_for_finding(finding)
        try:
            finding.doc_references = await enrich_references(topic)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Doc enrichment failed for finding %s: %s", finding.finding_id, exc)

        if finding.action_type == ActionType.SAFE_AUTO:
            finding.status = FindingStatus.SANDBOX_TESTING
            _emit_activity(on_progress, cluster, "testing_sandbox", f"Testing '{finding.title}' in the WASM sandbox")
            finding.sandbox_test_result = await _sandbox_test(finding, stats)
            finding.status = FindingStatus.PENDING_APPROVAL
        else:
            finding.status = FindingStatus.SUGGESTED

        await store.save_finding(finding)
        created += 1

        if finding.action_type == ActionType.REQUIRES_CODE_CHANGE:
            # Fire-and-forget: see _draft_query_suggestion_background's
            # docstring for why this must not block the pass.
            _spawn_background(_draft_query_suggestion_background(finding.finding_id, store, on_progress))

        try:
            await memory.remember(
                MemoryTier.EPISODIC, "finding_raised",
                {"title": finding.title, "category": finding.category.value, "severity": finding.severity.value,
                 "action_type": finding.action_type.value},
                cluster_id=str(cluster.cluster_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write episodic memory for finding %s: %s", finding.finding_id, exc)

        if on_progress:
            on_progress("finding", {"cluster_id": str(cluster.cluster_id), "finding": finding.model_dump(mode="json")})

    cluster.last_analyzed_at = datetime.utcnow()
    await store.save_cluster(cluster)

    try:
        await memory.remember(
            MemoryTier.SHORT_TERM, "analysis_pass",
            {"queries_examined": len(stats.completed_requests), "indexes_examined": len(stats.index_catalog),
             "findings_created": created, "findings_updated": updated},
            cluster_id=str(cluster.cluster_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write short-term memory for analysis pass: %s", exc)

    _emit_activity(on_progress, cluster, "idle", f"Idle -- next analysis pass for {cluster.name} in {settings.analysis_interval_s}s")

    return AnalysisRunSummary(
        cluster_id=cluster.cluster_id,
        started_at=started,
        finished_at=datetime.utcnow(),
        findings_created=created,
        findings_updated=updated,
        queries_examined=len(stats.completed_requests),
        indexes_examined=len(stats.index_catalog),
    )
