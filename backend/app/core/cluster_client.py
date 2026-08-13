"""
Client for talking to a Couchbase cluster under management -- Enterprise
Edition (self-hosted) or Capella. This is deliberately separate from
app/memory/couchbase_memory.py, which talks to the agent's own memory
cluster; ClusterClient always talks to whatever cluster the operator
registered for analysis/optimization.

Query-service calls (system:completed_requests, system:indexes,
system:active_requests) go through the Couchbase Python SDK and work
identically against EE and Capella. Node/bucket resource stats (RAM quota,
resident ratio, disk) use the self-hosted Management REST API on port 8091,
which Capella does not expose publicly the same way -- for a Capella cluster,
resource-tier findings are skipped unless capella_cluster_id plus
CAPELLA_API_TOKEN/CAPELLA_ORG_ID are configured, in which case the Capella
Management API (v4) is used instead.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster as SDKCluster
from couchbase.options import ClusterOptions

from app.config import get_settings
from app.models.enums import ClusterKind
from app.models.schemas import Cluster

logger = logging.getLogger(__name__)


class ClusterUnreachableError(Exception):
    pass


class ClusterClient:
    def __init__(self, cluster: Cluster) -> None:
        self.cluster = cluster
        self.settings = get_settings()
        self._sdk: SDKCluster | None = None

    # -- connection ------------------------------------------------------------

    def _connect(self) -> SDKCluster:
        if self._sdk is not None:
            return self._sdk
        auth = PasswordAuthenticator(self.cluster.username, self.cluster.password)
        sdk = SDKCluster(self.cluster.connection_string, ClusterOptions(auth))
        # wait_until_ready requires a timedelta, not a bare int -- passing an
        # int raises AttributeError('int' object has no attribute
        # 'total_seconds') deep inside the SDK.
        sdk.wait_until_ready(timeout=timedelta(seconds=15))
        self._sdk = sdk
        return sdk

    def _management_url(self) -> Optional[str]:
        if self.cluster.management_url:
            return self.cluster.management_url.rstrip("/")
        if self.cluster.kind != ClusterKind.ENTERPRISE:
            return None
        parsed = urlparse(self.cluster.connection_string.replace("couchbases://", "https://").replace("couchbase://", "http://"))
        host = parsed.hostname or parsed.path
        scheme = "https" if self.cluster.connection_string.startswith("couchbases") else "http"
        port = 18091 if scheme == "https" else 8091
        return f"{scheme}://{host}:{port}"

    async def test_connection(self) -> dict[str, Any]:
        """Returns basic topology info: version, edition, node count."""
        try:
            sdk = self._connect()
            sdk.query("SELECT 1 AS ok").execute()
        except Exception as exc:  # noqa: BLE001
            raise ClusterUnreachableError(str(exc)) from exc

        info: dict[str, Any] = {"reachable": True}
        mgmt = self._management_url()
        if mgmt:
            try:
                async with httpx.AsyncClient(timeout=10, verify=True) as client:
                    resp = await client.get(
                        f"{mgmt}/pools/default", auth=(self.cluster.username, self.cluster.password)
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    info["node_count"] = len(data.get("nodes", []))
                    if data.get("nodes"):
                        info["version"] = data["nodes"][0].get("version")
            except Exception as exc:  # noqa: BLE001
                logger.info("Management REST not reachable for %s (non-fatal): %s", self.cluster.name, exc)
        return info

    # -- query-service introspection (SDK; works for EE and Capella) ---------

    def completed_requests(self, limit: int) -> list[dict[str, Any]]:
        sdk = self._connect()
        q = (
            "SELECT statement, elapsedTime, serviceTime, resultCount, resultSize, "
            "scanConsistency, state, requestTime, phaseCounts, phaseTimes, preparedText, "
            "errorCount, node, usedMemory "
            "FROM system:completed_requests "
            # Exclude the agent's own introspection queries (this one included, plus
            # index_catalog()'s system:indexes query, etc.) -- left in, they get
            # analyzed as if they were application traffic (a primary scan against
            # the system: pseudo-keyspace has no index to fix, so the rule engine
            # was raising nonsense "Primary index scan on `system`" findings), and
            # they crowd real application query history out of this bounded,
            # LIMIT'd window over repeated analysis passes.
            "WHERE LOWER(IFMISSINGORNULL(statement, '')) NOT LIKE '%system:%' "
            f"ORDER BY requestTime DESC LIMIT {int(limit)}"
        )
        try:
            return [dict(row) for row in sdk.query(q).execute()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("completed_requests query failed for %s: %s", self.cluster.name, exc)
            return []

    def index_catalog(self) -> list[dict[str, Any]]:
        sdk = self._connect()
        q = (
            "SELECT s.name, s.id, s.bucket_id, s.scope_id, s.keyspace_id, s.state, "
            "s.is_primary, s.num_replica, s.`using`, s.condition, s.`partition` "
            "FROM system:indexes AS s"
        )
        try:
            return [dict(row) for row in sdk.query(q).execute()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("system:indexes query failed for %s: %s", self.cluster.name, exc)
            return []

    def bucket_names(self) -> list[str]:
        sdk = self._connect()
        try:
            return [dict(row)["name"] for row in sdk.query("SELECT name FROM system:keyspaces WHERE `is_bucket` = true").execute()]
        except Exception:  # noqa: BLE001
            try:
                return [b for b in sdk.buckets().get_all_buckets()]
            except Exception as exc:  # noqa: BLE001
                logger.warning("bucket listing failed for %s: %s", self.cluster.name, exc)
                return []

    # -- RBAC introspection (self-hosted EE via Management REST) --------------

    async def fetch_granted_roles(self) -> tuple[Optional[list[str]], Optional[str]]:
        """Best-effort comparison of the cluster's declared access_mode
        against the Couchbase roles actually granted to the registered
        username. Returns (granted_roles, note). Both are None if the check
        couldn't be performed (Capella, or Management REST unreachable/
        unauthorized for this user) -- that's expected and non-fatal; the
        declared access_mode still governs enforcement in core/optimizer.py
        regardless of what this finds.
        """
        mgmt = self._management_url()
        if not mgmt:
            return None, (
                "Role check skipped: Capella database-access-credential roles aren't "
                "introspectable over this API. Confirm the credential's assigned access "
                "level in the Capella UI matches the access mode selected here."
            )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{mgmt}/settings/rbac/users/local/{self.cluster.username}",
                    auth=(self.cluster.username, self.cluster.password),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.info("RBAC role check unavailable for %s (non-fatal): %s", self.cluster.name, exc)
            return None, f"Role check skipped: Management REST unreachable ({exc})."

        roles = sorted({r.get("role", "") for r in data.get("roles", []) if r.get("role")})

        write_roles = {"cluster_admin", "bucket_admin", "query_manage_index", "admin"}
        read_roles = {"ro_admin", "query_system_catalog", "query_select", "data_reader"}
        has_write = any(r in write_roles for r in roles)
        has_read = any(r in read_roles for r in roles)

        declared = self.cluster.access_mode
        from app.models.enums import AccessMode  # local import avoids a cycle at module load

        if declared == AccessMode.READ_WRITE and not has_write:
            note = (
                f"Declared read/write, but granted roles ({', '.join(roles) or 'none detected'}) "
                "don't include an index/bucket-admin role -- SAFE_AUTO apply calls will likely be "
                "rejected by the cluster itself even though the agent will attempt them."
            )
        elif declared == AccessMode.READ_ONLY and has_write:
            note = (
                f"Granted roles ({', '.join(roles)}) include write access, but this cluster is "
                "declared read-only here -- the agent will still refuse to apply changes; switch "
                "the mode above if you want it to."
            )
        elif not has_read and not has_write:
            note = (
                f"Granted roles ({', '.join(roles) or 'none detected'}) don't obviously include "
                "query/catalog read access -- analysis may fail. See README 'Cluster access & "
                "permissions'."
            )
        else:
            note = f"Granted roles: {', '.join(roles) or 'none detected'} -- consistent with declared mode."

        return roles, note

    # -- resource stats (self-hosted EE via Management REST) ------------------

    async def node_and_bucket_stats(self) -> dict[str, Any]:
        mgmt = self._management_url()
        if not mgmt:
            return {}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                pools = await client.get(f"{mgmt}/pools/default", auth=(self.cluster.username, self.cluster.password))
                pools.raise_for_status()
                pdata = pools.json()

                buckets_resp = await client.get(
                    f"{mgmt}/pools/default/buckets", auth=(self.cluster.username, self.cluster.password)
                )
                buckets_resp.raise_for_status()
                buckets = buckets_resp.json()

            nodes = [
                {
                    "hostname": n.get("hostname"),
                    "cpu_utilization_rate": (n.get("systemStats") or {}).get("cpu_utilization_rate"),
                    "mem_free": (n.get("systemStats") or {}).get("mem_free"),
                    "mem_total": (n.get("systemStats") or {}).get("mem_total"),
                    "status": n.get("status"),
                }
                for n in pdata.get("nodes", [])
            ]
            bucket_summaries = [
                {
                    "name": b.get("name"),
                    "ram_quota_mb": (b.get("quota") or {}).get("ram", 0) // (1024 * 1024),
                    "basic_stats": b.get("basicStats", {}),
                }
                for b in buckets
            ]
            return {"nodes": nodes, "buckets": bucket_summaries}
        except Exception as exc:  # noqa: BLE001
            logger.info("Resource stats unavailable for %s (non-fatal): %s", self.cluster.name, exc)
            return {}

    # -- safe apply (SAFE_AUTO findings only) ---------------------------------

    def execute_statement(self, statement: str) -> dict[str, Any]:
        """Runs a single N1QL/DDL statement (e.g. CREATE INDEX). Only ever
        called for findings classified ActionType.SAFE_AUTO after human
        approval -- see core/optimizer.py."""
        sdk = self._connect()
        result = sdk.query(statement).execute()
        return {"metrics": getattr(result, "metadata", lambda: None)() and str(result.metadata().metrics())}

    def close(self) -> None:
        if self._sdk is not None:
            self._sdk.close()
            self._sdk = None
