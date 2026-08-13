"""
Client-shape adapter for clusters backed by an uploaded, offline Couchbase
support bundle instead of a live connection. Deliberately duck-types the
same subset of ClusterClient's interface that core/analyzer.py's
gather_stats() calls (completed_requests / index_catalog / bucket_names /
node_and_bucket_stats / close), reading from a snapshot that was parsed once
at upload time -- see core/bundle_parser.py for how that snapshot is built.

A bundle is a point-in-time capture, not a live source: re-running analysis
re-runs the rule engine against the *same* cached snapshot rather than
pulling anything fresh. Upload a newer bundle (same cluster_id, via the
Clusters page) to refresh it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models.schemas import Cluster

logger = logging.getLogger(__name__)


class BundleUnavailableError(Exception):
    pass


class BundleClusterClient:
    def __init__(self, cluster: Cluster) -> None:
        self.cluster = cluster
        self.settings = get_settings()

    def snapshot_path(self) -> Path:
        return Path(self.settings.bundle_storage_dir) / "snapshots" / f"{self.cluster.cluster_id}.json"

    def _load(self) -> dict[str, Any]:
        p = self.snapshot_path()
        if not p.exists():
            raise BundleUnavailableError(
                f"No parsed support-bundle snapshot found for cluster '{self.cluster.name}' -- "
                "upload a bundle for it again from the Clusters page."
            )
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise BundleUnavailableError(f"Could not read cached bundle snapshot: {exc}") from exc

    async def test_connection(self) -> dict[str, Any]:
        snap = self._load()
        return {
            "reachable": True,
            "source": "support_bundle",
            "queries_found": len(snap.get("completed_requests", [])),
            "indexes_found": len(snap.get("index_catalog", [])),
        }

    def completed_requests(self, limit: int) -> list[dict[str, Any]]:
        return self._load().get("completed_requests", [])[: int(limit)]

    def index_catalog(self) -> list[dict[str, Any]]:
        return self._load().get("index_catalog", [])

    def bucket_names(self) -> list[str]:
        return self._load().get("bucket_names", [])

    async def node_and_bucket_stats(self) -> dict[str, Any]:
        return self._load().get("resource_stats", {})

    def execute_statement(self, statement: str) -> dict[str, Any]:  # noqa: ARG002
        """Should never be reached -- core/optimizer.py refuses to
        approve/apply anything against a SUPPORT_BUNDLE cluster before it
        gets here. Left as a hard failure rather than a silent no-op in case
        that guard is ever bypassed."""
        raise RuntimeError(
            "This cluster is backed by a static support bundle -- there is no live connection to run "
            "statements against. Register it as a live cluster to apply changes."
        )

    def close(self) -> None:
        pass
