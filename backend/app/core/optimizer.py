"""Approve / reject / apply workflow for findings.

Only ActionType.SAFE_AUTO findings can ever reach `apply()`, and only after a
human has approved them through `approve()` -- there is no code path from
"the agent detected this" straight to "the agent changed the cluster." This
mirrors the approval gate the reference UI style is built around: a named
approver, a timestamp, and an explicit confirmation, recorded before anything
executes.

REQUIRES_CODE_CHANGE findings never have a suggested_action and therefore can
never be approved/applied here -- see FindingStatus.SUGGESTED, which is their
only terminal state; the agent's job for those is to explain and cite
sources, not to act.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.cluster_client import ClusterClient
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import AccessMode, ActionType, ClusterSourceType, FindingStatus, MemoryTier
from app.models.schemas import Cluster, Finding

logger = logging.getLogger(__name__)


class OptimizerError(Exception):
    pass


def _require_read_write(cluster: Cluster) -> None:
    """Server-side enforcement: no SAFE_AUTO change is ever approved or
    applied against a cluster the operator hasn't explicitly switched to
    read/write, no matter what a client sends. This is the only gate that
    matters -- the frontend disabling the Approve button (see
    FindingCard.tsx) is a UX courtesy, not the enforcement point."""
    if cluster.source_type == ClusterSourceType.SUPPORT_BUNDLE:
        raise OptimizerError(
            f"'{cluster.name}' is a static support-bundle snapshot, not a live cluster -- there is "
            "nothing to apply a change to. Register the cluster as a live connection to approve/apply "
            "SAFE_AUTO changes."
        )
    if cluster.access_mode != AccessMode.READ_WRITE:
        raise OptimizerError(
            f"Cluster '{cluster.name}' is registered as read-only. Switch it to read/write on the "
            "Clusters page (and confirm the credential actually has write-capable Couchbase roles -- "
            "see README 'Cluster access & permissions') before approving or applying SAFE_AUTO changes."
        )


async def approve(finding: Finding, approved_by: str, note: str | None = None, cluster: Cluster | None = None) -> Finding:
    if finding.action_type != ActionType.SAFE_AUTO:
        raise OptimizerError("Only SAFE_AUTO findings can be approved for auto-apply.")
    if finding.status not in (FindingStatus.PENDING_APPROVAL, FindingStatus.OPEN):
        raise OptimizerError(f"Finding is in status '{finding.status.value}', not eligible for approval.")
    if cluster is not None:
        _require_read_write(cluster)

    finding.status = FindingStatus.APPROVED
    finding.approved_by = approved_by
    finding.approved_at = datetime.utcnow()

    try:
        await AgentMemoryStore.instance().remember(
            MemoryTier.EPISODIC, "finding_approved",
            {"title": finding.title, "approved_by": approved_by, "note": note},
            cluster_id=str(finding.cluster_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write approval memory: %s", exc)

    return finding


async def reject(finding: Finding, rejected_by: str, reason: str | None = None) -> Finding:
    finding.status = FindingStatus.REJECTED
    finding.apply_result = f"Rejected by {rejected_by}" + (f": {reason}" if reason else "")

    try:
        await AgentMemoryStore.instance().remember(
            MemoryTier.EPISODIC, "finding_rejected",
            {"title": finding.title, "rejected_by": rejected_by, "reason": reason},
            cluster_id=str(finding.cluster_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write rejection memory: %s", exc)

    return finding


async def apply(finding: Finding, cluster: Cluster) -> Finding:
    if finding.status != FindingStatus.APPROVED:
        raise OptimizerError(f"Finding must be approved before applying (current status: {finding.status.value}).")
    if not finding.suggested_action:
        raise OptimizerError("Finding has no suggested_action to apply.")
    _require_read_write(cluster)

    action = finding.suggested_action
    kind = action.get("kind")
    client = ClusterClient(cluster)
    try:
        if kind == "n1ql_statement":
            client.execute_statement(action["statement"])
            finding.apply_result = f"Executed: {action['statement']}"
        elif kind == "rest_setting":
            await _apply_rest_setting(cluster, action)
            finding.apply_result = f"Applied setting via {action.get('endpoint')}: {action.get('payload')}"
        else:
            raise OptimizerError(f"Unknown suggested_action kind: {kind}")
        finding.status = FindingStatus.APPLIED
        finding.applied_at = datetime.utcnow()
    except Exception as exc:  # noqa: BLE001
        finding.status = FindingStatus.APPLY_FAILED
        finding.apply_result = f"Apply failed: {exc}"
        logger.error("Applying finding %s failed: %s", finding.finding_id, exc)
    finally:
        client.close()

    try:
        await AgentMemoryStore.instance().remember(
            MemoryTier.EPISODIC, "finding_applied",
            {"title": finding.title, "status": finding.status.value, "result": finding.apply_result},
            cluster_id=str(finding.cluster_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write apply memory: %s", exc)

    return finding


async def _apply_rest_setting(cluster: Cluster, action: dict) -> None:
    import httpx
    from app.core.cluster_client import ClusterClient as _CC

    mgmt = _CC(cluster)._management_url()  # noqa: SLF001
    if not mgmt:
        raise OptimizerError(
            "This cluster has no reachable Management REST endpoint (Capella clusters need "
            "CAPELLA_API_TOKEN/CAPELLA_ORG_ID configured for settings changes) -- apply manually."
        )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(
            action.get("method", "POST"),
            f"{mgmt}{action['endpoint']}",
            data=action.get("payload", {}),
            auth=(cluster.username, cluster.password),
        )
        resp.raise_for_status()
