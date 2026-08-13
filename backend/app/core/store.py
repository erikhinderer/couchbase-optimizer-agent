"""
Lightweight persistence for registered clusters and findings. Deliberately
simple (a JSON file on a mounted volume) so the backend has no hard
dependency on an additional database beyond the Couchbase EE instance already
used for agent memory. Swap this for a Couchbase collection or Postgres table
if you need multiple backend replicas.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.models.schemas import Cluster, Finding

logger = logging.getLogger(__name__)


class StateStore:
    _instance: "StateStore | None" = None

    def __init__(self) -> None:
        self.settings = get_settings()
        self._lock = asyncio.Lock()
        self._clusters: dict[str, Cluster] = {}
        self._findings: dict[str, Finding] = {}
        self._load()

    @classmethod
    def instance(cls) -> "StateStore":
        if cls._instance is None:
            cls._instance = StateStore()
        return cls._instance

    def _path(self) -> Path:
        return Path(self.settings.state_file)

    def _load(self) -> None:
        p = self._path()
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text())
            for cid, data in raw.get("clusters", {}).items():
                self._clusters[cid] = Cluster.model_validate(data)
            for fid, data in raw.get("findings", {}).items():
                self._findings[fid] = Finding.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load state file %s: %s", p, exc)

    def _persist(self) -> None:
        p = self._path()
        os.makedirs(p.parent, exist_ok=True)
        payload = {
            "clusters": {cid: json.loads(c.model_dump_json()) for cid, c in self._clusters.items()},
            "findings": {fid: json.loads(f.model_dump_json()) for fid, f in self._findings.items()},
        }
        p.write_text(json.dumps(payload, indent=2, default=str))

    # -- clusters ------------------------------------------------------------

    async def save_cluster(self, cluster: Cluster) -> None:
        async with self._lock:
            self._clusters[str(cluster.cluster_id)] = cluster
            self._persist()

    async def get_cluster(self, cluster_id: UUID | str) -> Cluster | None:
        async with self._lock:
            return self._clusters.get(str(cluster_id))

    async def list_clusters(self) -> list[Cluster]:
        async with self._lock:
            return sorted(self._clusters.values(), key=lambda c: c.created_at, reverse=True)

    async def delete_cluster(self, cluster_id: UUID | str) -> None:
        async with self._lock:
            self._clusters.pop(str(cluster_id), None)
            # Cascade: findings are keyed by cluster_id, and re-registering a
            # cluster (even under the same name) always gets a fresh
            # cluster_id (Cluster.cluster_id uses default_factory=uuid4).
            # Without this, a deleted cluster's findings silently become
            # orphaned garbage in state.json -- invisible in the UI (every
            # list_findings call filters by the currently-registered
            # cluster_id) but never actually removed, and a delete+re-add of
            # the "same" cluster looks like the agent forgot everything it
            # had found even though nothing was approved or rejected.
            orphaned = [fid for fid, f in self._findings.items() if str(f.cluster_id) == str(cluster_id)]
            for fid in orphaned:
                self._findings.pop(fid, None)
            self._persist()

    # -- findings --------------------------------------------------------------

    async def save_finding(self, finding: Finding) -> None:
        async with self._lock:
            self._findings[str(finding.finding_id)] = finding
            self._persist()

    async def get_finding(self, finding_id: UUID | str) -> Finding | None:
        async with self._lock:
            return self._findings.get(str(finding_id))

    async def list_findings(self, cluster_id: UUID | str | None = None) -> list[Finding]:
        async with self._lock:
            values = list(self._findings.values())
            if cluster_id is not None:
                values = [f for f in values if str(f.cluster_id) == str(cluster_id)]
            return sorted(values, key=lambda f: f.detected_at, reverse=True)

    async def find_open_duplicate(self, cluster_id: UUID | str, title: str) -> Finding | None:
        """Findings are re-detected on every analysis pass -- avoid piling up
        duplicate open findings for the same recurring issue."""
        async with self._lock:
            for f in self._findings.values():
                if (
                    str(f.cluster_id) == str(cluster_id)
                    and f.title == title
                    and f.status.value in ("open", "sandbox_testing", "pending_approval", "suggested")
                ):
                    return f
            return None
