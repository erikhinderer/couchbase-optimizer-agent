"""On-demand analysis: the scheduler already re-analyzes every registered
cluster continuously (see core/scheduler.py), but the UI also exposes a
"run now" button that goes through this same run_analysis() call path."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.analyzer import run_analysis
from app.core.store import StateStore
from app.models.schemas import AnalysisRunSummary
from app.websocket.events import broadcast, broadcast_sync

router = APIRouter()


@router.post("/{cluster_id}/run", response_model=AnalysisRunSummary)
async def run_now(cluster_id: UUID) -> AnalysisRunSummary:
    cluster = await StateStore.instance().get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    # Forward the same "analyzing / validating / testing_sandbox / idle"
    # activity events the scheduler's continuous passes emit, so a manual
    # "Run now" also drives the sidebar status indicator.
    summary = await run_analysis(cluster, on_progress=lambda t, p: broadcast_sync(t, p))
    await broadcast("analysis_complete", summary.model_dump(mode="json"))
    return summary


@router.get("/{cluster_id}/snapshot")
async def snapshot(cluster_id: UUID) -> dict:
    """Lightweight, read-only stats snapshot for the Dashboard/Indexes pages
    -- distinct from run_now(), which persists findings. This never writes
    anything; it just gives the UI something to chart between analysis
    passes."""
    from app.core.analyzer import gather_stats
    from app.core.cluster_client import ClusterClient
    from app.core.rules.base import elapsed_ms, group_by_normalized_statement
    from app.config import get_settings

    cluster = await StateStore.instance().get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    client = ClusterClient(cluster)
    try:
        stats = await gather_stats(client, get_settings().completed_requests_lookback)
    finally:
        client.close()

    durations = [elapsed_ms(r) for r in stats.completed_requests]
    buckets = {"0-100ms": 0, "100ms-1s": 0, "1-5s": 0, "5-30s": 0, ">30s": 0}
    for d in durations:
        if d < 100:
            buckets["0-100ms"] += 1
        elif d < 1000:
            buckets["100ms-1s"] += 1
        elif d < 5000:
            buckets["1-5s"] += 1
        elif d < 30000:
            buckets["5-30s"] += 1
        else:
            buckets[">30s"] += 1

    primary_count = sum(1 for r in stats.completed_requests if (r.get("phaseCounts") or {}).get("primaryScan"))
    index_count = sum(1 for r in stats.completed_requests if (r.get("phaseCounts") or {}).get("indexScan"))
    total = len(stats.completed_requests) or 1

    groups = group_by_normalized_statement(stats.completed_requests)
    top_statements = sorted(
        (
            {"normalized_statement": k, "count": len(v), "avg_elapsed_ms": round(sum(elapsed_ms(r) for r in v) / len(v), 1)}
            for k, v in groups.items()
        ),
        key=lambda x: x["count"], reverse=True,
    )[:10]

    return {
        "cluster_id": str(cluster_id),
        "queries_examined": len(stats.completed_requests),
        "duration_distribution": buckets,
        "scan_type_breakdown": {
            "primary": primary_count,
            "index": index_count,
            "other": max(total - primary_count - index_count, 0),
        },
        "index_catalog": stats.index_catalog,
        "top_statements": top_statements,
        "resource_stats": stats.resource_stats,
        "bucket_names": stats.bucket_names,
    }
