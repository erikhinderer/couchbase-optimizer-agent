"""Findings: list, inspect, approve, reject, and apply. Approve/apply are two
separate steps on purpose -- approval records who signed off and when;
apply is the (also logged) act of actually running the change."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.core import optimizer
from app.core.store import StateStore
from app.models.schemas import ApprovalRequest, Finding, RejectionRequest
from app.websocket.events import broadcast

router = APIRouter()


@router.get("", response_model=list[Finding])
async def list_findings(cluster_id: UUID | None = Query(default=None)) -> list[Finding]:
    return await StateStore.instance().list_findings(cluster_id)


@router.get("/{finding_id}", response_model=Finding)
async def get_finding(finding_id: UUID) -> Finding:
    finding = await StateStore.instance().get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    return finding


@router.post("/{finding_id}/approve", response_model=Finding)
async def approve_finding(finding_id: UUID, body: ApprovalRequest) -> Finding:
    if body.finding_id != finding_id:
        raise HTTPException(400, "finding_id mismatch between path and body")
    store = StateStore.instance()
    finding = await store.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    cluster = await store.get_cluster(finding.cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster for this finding no longer exists")
    try:
        finding = await optimizer.approve(finding, body.approved_by, body.note, cluster)
    except optimizer.OptimizerError as exc:
        raise HTTPException(400, str(exc)) from exc
    await store.save_finding(finding)
    await broadcast("finding_approved", finding.model_dump(mode="json"))
    return finding


@router.post("/{finding_id}/reject", response_model=Finding)
async def reject_finding(finding_id: UUID, body: RejectionRequest) -> Finding:
    if body.finding_id != finding_id:
        raise HTTPException(400, "finding_id mismatch between path and body")
    store = StateStore.instance()
    finding = await store.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    finding = await optimizer.reject(finding, body.rejected_by, body.reason)
    await store.save_finding(finding)
    await broadcast("finding_rejected", finding.model_dump(mode="json"))
    return finding


@router.post("/{finding_id}/apply", response_model=Finding)
async def apply_finding(finding_id: UUID) -> Finding:
    store = StateStore.instance()
    finding = await store.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    cluster = await store.get_cluster(finding.cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster for this finding no longer exists")

    await broadcast("agent_activity", {
        "cluster_id": str(cluster.cluster_id), "state": "applying", "message": f"Applying '{finding.title}'",
    })
    try:
        finding = await optimizer.apply(finding, cluster)
    except optimizer.OptimizerError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await broadcast("agent_activity", {
            "cluster_id": str(cluster.cluster_id), "state": "idle", "message": f"Idle -- {cluster.name}",
        })

    await store.save_finding(finding)
    await broadcast("finding_applied", finding.model_dump(mode="json"))
    return finding
