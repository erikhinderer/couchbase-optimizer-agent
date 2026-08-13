"""Pydantic request/response/domain models shared across the API and engine."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.enums import (
    AccessMode,
    ActionType,
    ClusterKind,
    ClusterSourceType,
    ClusterStatus,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
    MemoryTier,
)


class DocReference(BaseModel):
    """A source documentation link shown under a recommendation for validation."""

    title: str
    url: str
    snippet: Optional[str] = None


class ClusterCreate(BaseModel):
    name: str
    kind: ClusterKind
    connection_string: str  # couchbases://host or Capella connection string
    username: str
    password: str
    # Read-only by default -- the operator has to explicitly opt a cluster
    # into read/write before the agent will ever apply a SAFE_AUTO change to
    # it. See README "Cluster access & permissions" for the Couchbase roles
    # each mode requires.
    access_mode: AccessMode = AccessMode.READ_ONLY
    management_url: Optional[str] = None  # defaults derived from connection_string for EE
    capella_cluster_id: Optional[str] = None  # only for kind=capella, enables Capella Mgmt API stats


class Cluster(BaseModel):
    cluster_id: UUID = Field(default_factory=uuid4)
    name: str
    kind: ClusterKind
    connection_string: str
    username: str
    password: str
    access_mode: AccessMode = AccessMode.READ_ONLY
    management_url: Optional[str] = None
    capella_cluster_id: Optional[str] = None
    status: ClusterStatus = ClusterStatus.UNKNOWN
    # Best-effort result of comparing the declared access_mode against the
    # Couchbase RBAC roles actually granted to `username` (self-hosted EE
    # only -- Capella's database-access-credential roles aren't introspectable
    # the same way). None if never checked or unreachable; never blocks
    # anything on its own, just informs the operator and the chat agent.
    granted_roles: Optional[list[str]] = None
    access_mode_note: Optional[str] = None
    # LIVE (default) connects over the network per access_mode above.
    # SUPPORT_BUNDLE is a static snapshot parsed from an uploaded
    # cbcollect_info archive -- see core/bundle_parser.py / bundle_client.py.
    # Always forced READ_ONLY server-side regardless of access_mode's value.
    source_type: ClusterSourceType = ClusterSourceType.LIVE
    bundle_filename: Optional[str] = None
    bundle_uploaded_at: Optional[datetime] = None
    bundle_parse_note: Optional[str] = None
    last_analyzed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def safe(self) -> "ClusterPublic":
        return ClusterPublic(
            cluster_id=self.cluster_id,
            name=self.name,
            kind=self.kind,
            connection_string=self.connection_string,
            access_mode=self.access_mode,
            granted_roles=self.granted_roles,
            access_mode_note=self.access_mode_note,
            source_type=self.source_type,
            bundle_filename=self.bundle_filename,
            bundle_uploaded_at=self.bundle_uploaded_at,
            bundle_parse_note=self.bundle_parse_note,
            status=self.status,
            last_analyzed_at=self.last_analyzed_at,
            created_at=self.created_at,
        )


class ClusterPublic(BaseModel):
    """Cluster shape returned to the frontend -- never includes credentials."""

    cluster_id: UUID
    name: str
    kind: ClusterKind
    connection_string: str
    access_mode: AccessMode
    granted_roles: Optional[list[str]] = None
    access_mode_note: Optional[str] = None
    source_type: ClusterSourceType
    bundle_filename: Optional[str] = None
    bundle_uploaded_at: Optional[datetime] = None
    bundle_parse_note: Optional[str] = None
    status: ClusterStatus
    last_analyzed_at: Optional[datetime]
    created_at: datetime


class SandboxTestResult(BaseModel):
    ran_at: datetime = Field(default_factory=datetime.utcnow)
    passed: bool
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    fuel_consumed: Optional[int] = None


class Finding(BaseModel):
    """A single optimization opportunity detected for a cluster."""

    finding_id: UUID = Field(default_factory=uuid4)
    cluster_id: UUID
    category: FindingCategory
    severity: FindingSeverity
    action_type: ActionType
    status: FindingStatus = FindingStatus.OPEN
    title: str
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    # For SAFE_AUTO findings: the concrete statement/operation the agent would
    # run if approved (e.g. a CREATE INDEX statement, a bucket setting patch).
    suggested_action: Optional[dict[str, Any]] = None
    # For REQUIRES_CODE_CHANGE findings: what the application team needs to do.
    code_change_guidance: Optional[str] = None
    doc_references: list[DocReference] = Field(default_factory=list)
    sandbox_test_result: Optional[SandboxTestResult] = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    apply_result: Optional[str] = None


class ApprovalRequest(BaseModel):
    finding_id: UUID
    approved_by: str
    confirm_reviewed: bool = True
    note: Optional[str] = None


class RejectionRequest(BaseModel):
    finding_id: UUID
    rejected_by: str
    reason: Optional[str] = None


class AnalysisRunSummary(BaseModel):
    cluster_id: UUID
    started_at: datetime
    finished_at: datetime
    findings_created: int
    findings_updated: int
    queries_examined: int
    indexes_examined: int


class ChatRequest(BaseModel):
    message: str
    cluster_id: Optional[UUID] = None


class ChatResponse(BaseModel):
    reply: str
    doc_references: list[DocReference] = Field(default_factory=list)
    recalled_memories: int = 0


class MemoryItem(BaseModel):
    memory_id: str
    tier: MemoryTier
    kind: str
    cluster_id: Optional[str] = None
    text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    score: Optional[float] = None
