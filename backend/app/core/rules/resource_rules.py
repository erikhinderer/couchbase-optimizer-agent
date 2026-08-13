"""Resource-pressure findings from node/bucket stats (self-hosted EE via
Management REST; skipped gracefully when unavailable, e.g. Capella clusters
without CAPELLA_API_TOKEN/CAPELLA_ORG_ID configured)."""
from __future__ import annotations

from uuid import UUID

from app.core.rules.base import ClusterStats
from app.models.enums import ActionType, FindingCategory, FindingSeverity
from app.models.schemas import Finding

RAM_QUOTA_WARN_PCT = 85.0
RAM_QUOTA_CRITICAL_PCT = 95.0
CPU_WARN_PCT = 80.0
CPU_CRITICAL_PCT = 92.0


def detect_bucket_memory_pressure(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    findings: list[Finding] = []
    for bucket in stats.resource_stats.get("buckets", []):
        basic = bucket.get("basic_stats") or {}
        quota_pct = basic.get("quotaPercentUsed")
        if quota_pct is None:
            continue
        if quota_pct < RAM_QUOTA_WARN_PCT:
            continue

        severity = FindingSeverity.CRITICAL if quota_pct >= RAM_QUOTA_CRITICAL_PCT else FindingSeverity.WARNING
        name = bucket.get("name")
        current_quota = bucket.get("ram_quota_mb", 0)
        proposed_quota = int(current_quota * 1.25) if current_quota else None

        suggested_action = None
        if proposed_quota:
            suggested_action = {
                "kind": "rest_setting",
                "method": "POST",
                "endpoint": f"/pools/default/buckets/{name}",
                "payload": {"ramQuotaMB": proposed_quota},
                "description": f"Raise `{name}` RAM quota from {current_quota}MB to {proposed_quota}MB.",
            }

        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.RESOURCE,
            severity=severity,
            action_type=ActionType.SAFE_AUTO if suggested_action else ActionType.REQUIRES_CODE_CHANGE,
            title=f"Bucket `{name}` is near its memory quota ({quota_pct:.0f}% used)",
            description=(
                f"Bucket `{name}` is using {quota_pct:.0f}% of its {current_quota}MB RAM quota. Above the "
                "high watermark, Couchbase starts ejecting items to stay under quota, which increases disk "
                "reads for subsequent access to those documents."
            ),
            evidence={"bucket": name, "quota_pct_used": quota_pct, "ram_quota_mb": current_quota},
            suggested_action=suggested_action,
            code_change_guidance=None if suggested_action else (
                "No node has enough free memory headroom to safely raise this bucket's quota further -- "
                "add a node or reduce another bucket's quota before increasing this one."
            ),
        ))
    return findings


def detect_high_cpu(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    findings: list[Finding] = []
    for node in stats.resource_stats.get("nodes", []):
        cpu = node.get("cpu_utilization_rate")
        if cpu is None or cpu < CPU_WARN_PCT:
            continue
        severity = FindingSeverity.CRITICAL if cpu >= CPU_CRITICAL_PCT else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.RESOURCE,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title=f"Node `{node.get('hostname')}` is running hot ({cpu:.0f}% CPU)",
            description=(
                f"Node `{node.get('hostname')}` has been observed at {cpu:.0f}% CPU utilization. Sustained "
                "high CPU increases query and mutation latency cluster-wide and reduces headroom for "
                "rebalance or failover recovery."
            ),
            evidence={"hostname": node.get("hostname"), "cpu_utilization_rate": cpu},
            code_change_guidance=(
                "This is a capacity decision (add a node, rebalance services, or move a heavy workload off "
                "this node) rather than something the agent can safely apply on its own -- review alongside "
                "the query/index findings above, since reducing primary scans often reduces CPU directly."
            ),
        ))
    return findings


def detect(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    return [
        *detect_bucket_memory_pressure(cluster_id, stats),
        *detect_high_cpu(cluster_id, stats),
    ]
