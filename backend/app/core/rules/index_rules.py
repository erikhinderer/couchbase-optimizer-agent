"""Index-related findings: primary-scan-heavy query shapes, unreplicated
indexes, indexes with no observed usage, slow index-scan phases, and
ORDER BY / LIMIT / OFFSET over-scan."""
from __future__ import annotations

import re
from uuid import UUID

from app.core.rules.base import (
    ClusterStats,
    elapsed_ms,
    group_by_normalized_statement,
    guess_simple_equality_field,
    guess_target_keyspace,
    normalize_statement,
    phase_count,
    phase_ms,
)
from app.models.enums import ActionType, FindingCategory, FindingSeverity
from app.models.schemas import Finding

PRIMARY_SCAN_WARN_PCT = 10.0
PRIMARY_SCAN_CRITICAL_PCT = 30.0
SLOW_INDEX_SCAN_MS = 300.0
_OFFSET_RE = re.compile(r"\bOFFSET\s+(\d+)", re.IGNORECASE)
_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


def detect_primary_scan_heavy(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    total = len(stats.completed_requests)
    if total == 0:
        return []

    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        primary_scan_rows = [r for r in rows if (r.get("phaseCounts") or {}).get("primaryScan")]
        if not primary_scan_rows:
            continue
        pct_of_total = 100.0 * len(rows) / total
        if pct_of_total < 1.0:  # ignore rare/one-off statements
            continue

        severity = (
            FindingSeverity.CRITICAL if pct_of_total >= PRIMARY_SCAN_CRITICAL_PCT
            else FindingSeverity.WARNING if pct_of_total >= PRIMARY_SCAN_WARN_PCT
            else FindingSeverity.INFO
        )

        sample = rows[0]
        keyspace = guess_target_keyspace(sample.get("statement", ""))
        field_name = guess_simple_equality_field(sample.get("statement", ""))

        evidence = {
            "normalized_statement": normalized,
            "occurrence_count": len(rows),
            "pct_of_all_queries": round(pct_of_total, 1),
            "sample_statement": sample.get("statement"),
            "keyspace": keyspace,
        }

        if field_name and keyspace:
            index_name = f"idx_auto_{keyspace.strip('`').replace('.', '_')}_{field_name.replace('.', '_')}"
            findings.append(Finding(
                cluster_id=cluster_id,
                category=FindingCategory.INDEX,
                severity=severity,
                action_type=ActionType.SAFE_AUTO,
                title=f"Primary index scan on `{keyspace}` for a simple equality filter on `{field_name}`",
                description=(
                    f"{len(rows)} queries ({round(pct_of_total, 1)}% of all queries examined) use a full "
                    f"primary index scan against `{keyspace}` filtering on `{field_name}`. Primary scans read "
                    "every document in the keyspace, so cost scales with bucket size instead of result size. "
                    "Creating a secondary index on the filtered field lets the query planner narrow the scan "
                    "without any change to the query or application code."
                ),
                evidence=evidence,
                suggested_action={
                    "kind": "n1ql_statement",
                    "statement": (
                        f"CREATE INDEX `{index_name}` IF NOT EXISTS ON {keyspace}(`{field_name}`) "
                        f"WITH {{\"num_replica\": 1}}"
                    ),
                    "description": f"Create secondary index `{index_name}` on {keyspace}(`{field_name}`).",
                },
            ))
        else:
            findings.append(Finding(
                cluster_id=cluster_id,
                category=FindingCategory.INDEX,
                severity=severity,
                action_type=ActionType.REQUIRES_CODE_CHANGE,
                title=f"Primary index scan on `{keyspace or 'a keyspace'}` for a complex predicate",
                description=(
                    f"{len(rows)} queries ({round(pct_of_total, 1)}% of all queries examined) use a full "
                    "primary index scan and the WHERE clause is too complex (multiple predicates, OR, "
                    "function calls, or array access) for the agent to safely author a covering index "
                    "definition automatically."
                ),
                evidence=evidence,
                code_change_guidance=(
                    "Review the query's WHERE clause with the application team and design a purpose-built "
                    "secondary (or composite) index, or simplify the predicate so an index can serve it. "
                    "The agent can validate a proposed index in the WASM sandbox once one is drafted."
                ),
            ))

    return findings


def detect_missing_index_replicas(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    findings: list[Finding] = []
    for idx in stats.index_catalog:
        if idx.get("is_primary"):
            continue
        if idx.get("state") != "online":
            continue
        num_replica = idx.get("num_replica") or 0
        if num_replica >= 1:
            continue

        keyspace = idx.get("keyspace_id") or idx.get("bucket_id")
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.INDEX,
            severity=FindingSeverity.WARNING,
            action_type=ActionType.SAFE_AUTO,
            title=f"Index `{idx.get('name')}` on `{keyspace}` has no replica",
            description=(
                f"Index `{idx.get('name')}` is online with 0 replicas. A single index copy is a single point "
                "of failure for both availability and scan throughput -- if its node goes down or is under "
                "load, every query that depends on it either fails or queues."
            ),
            evidence={"index": idx},
            suggested_action={
                "kind": "n1ql_statement",
                "statement": f"ALTER INDEX `{keyspace}`.`{idx.get('name')}` WITH {{\"action\": \"replica_count\", \"num_replica\": 1}}",
                "description": f"Add one replica to index `{idx.get('name')}`.",
            },
        ))
    return findings


def detect_unused_indexes(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """Heuristic, not exact: an index is flagged only when its indexed field
    name never appears in the WHERE/ORDER BY text of any statement observed
    in the lookback window. completed_requests doesn't reliably name the
    index actually chosen by the planner pre-8.0, so this is a text-proximity
    signal meant to prompt a human look, not a certainty -- these findings
    stay REQUIRES_CODE_CHANGE-adjacent in spirit but are still SAFE_AUTO for
    apply purposes (DROP INDEX is a cluster-side DDL op, not an app change);
    severity is capped at WARNING and the evidence spells out the caveat."""
    findings: list[Finding] = []
    all_statement_text = " ".join(
        (r.get("statement") or "") for r in stats.completed_requests
    ).lower()

    for idx in stats.index_catalog:
        if idx.get("is_primary") or idx.get("state") != "online":
            continue
        index_keys = idx.get("using") or ""
        name = (idx.get("name") or "").lower()
        # Skip if we have too little query history to say anything meaningful.
        if len(stats.completed_requests) < 20:
            continue
        referenced_by_name = name and name in all_statement_text
        if referenced_by_name:
            continue

        keyspace = idx.get("keyspace_id") or idx.get("bucket_id")
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.INDEX,
            severity=FindingSeverity.WARNING,
            action_type=ActionType.SAFE_AUTO,
            title=f"Index `{idx.get('name')}` on `{keyspace}` shows no observed usage",
            description=(
                f"Index `{idx.get('name')}` was not referenced by name in any of the last "
                f"{len(stats.completed_requests)} completed queries examined. This is a text-proximity "
                "heuristic, not a certainty -- confirm with EXPLAIN or a longer observation window before "
                "approving removal, since low-frequency but important queries may still depend on it."
            ),
            evidence={"index": idx, "queries_examined": len(stats.completed_requests)},
            suggested_action={
                "kind": "n1ql_statement",
                "statement": f"DROP INDEX `{keyspace}`.`{idx.get('name')}`",
                "description": f"Drop index `{idx.get('name')}`.",
            },
        ))
    return findings


def detect_slow_index_scans(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """A query that IS using a secondary index but whose indexScan phase is
    still slow usually means the index isn't selective enough for this
    predicate -- a wide range scan, a leading key with low cardinality, or a
    missing composite key that would let the index (rather than a later
    fetch/filter step) do the filtering. Composite index design needs a
    human's judgment about the actual predicate shape, so this is a
    suggestion, not an auto-fix."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        scan_rows = [r for r in rows if phase_count(r, "indexScan") > 0]
        if len(scan_rows) < 3:
            continue
        avg_scan_ms = sum(phase_ms(r, "indexScan") for r in scan_rows) / len(scan_rows)
        if avg_scan_ms < SLOW_INDEX_SCAN_MS:
            continue

        sample = scan_rows[0]
        severity = FindingSeverity.CRITICAL if avg_scan_ms > 3 * SLOW_INDEX_SCAN_MS else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.INDEX,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title=f"Slow index-scan phase on `{guess_target_keyspace(sample.get('statement', '')) or 'a keyspace'}`",
            description=(
                f"{len(scan_rows)} executions of this query shape spend an average of {avg_scan_ms:.0f}ms in "
                "the indexScan phase alone -- the index is being used, but isn't narrowing the scan enough "
                "for this predicate, so a lot of index entries are still being walked before fetch/filter."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(scan_rows),
                "avg_index_scan_ms": round(avg_scan_ms, 1),
                "sample_statement": sample.get("statement"),
            },
            code_change_guidance=(
                "Review the index definition against this query's actual predicate -- a composite index "
                "matching the leading equality/range keys used here (or a covering index that includes the "
                "projected fields) will usually cut this down; the agent can validate a proposed index "
                "definition in the WASM sandbox once one is drafted."
            ),
        ))
    return findings


def detect_order_by_offset_overscan(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """OFFSET makes Couchbase's index/sort still produce and discard every
    skipped row before returning the page -- cost scales with offset+limit,
    not with the page size. Deep OFFSET pagination (offset-based 'page 40 of
    results') is the classic way this bites; keyset/cursor pagination is the
    fix, and that's a query-shape change the agent won't make unilaterally."""
    DEEP_OFFSET_THRESHOLD = 500
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        offset_match = _OFFSET_RE.search(sample_text)
        if not offset_match or not _ORDER_BY_RE.search(sample_text):
            continue
        offset_value = int(offset_match.group(1))
        if offset_value < DEEP_OFFSET_THRESHOLD:
            continue

        avg_elapsed = sum(elapsed_ms(r) for r in rows) / len(rows)
        severity = FindingSeverity.CRITICAL if offset_value >= 5 * DEEP_OFFSET_THRESHOLD else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.INDEX,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title=f"Deep OFFSET pagination forces large index over-scan (OFFSET {offset_value})",
            description=(
                f"{len(rows)} executions of this ORDER BY query use OFFSET {offset_value}, which means the "
                f"index/sort step has to produce and discard {offset_value} rows before the first one in the "
                f"page is returned -- averaging {avg_elapsed:.0f}ms elapsed for this shape."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "offset": offset_value,
                "avg_elapsed_ms": round(avg_elapsed, 1),
                "sample_statement": sample_text,
            },
            code_change_guidance=(
                "Switch to keyset (cursor-based) pagination -- carry the last-seen sort key/id forward as a "
                "WHERE predicate (e.g. `WHERE created_at < $last_seen ORDER BY created_at DESC LIMIT n`) "
                "instead of OFFSET, so cost stays proportional to the page size regardless of how deep the "
                "user pages."
            ),
        ))
    return findings


def detect(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    return [
        *detect_primary_scan_heavy(cluster_id, stats),
        *detect_missing_index_replicas(cluster_id, stats),
        *detect_unused_indexes(cluster_id, stats),
        *detect_slow_index_scans(cluster_id, stats),
        *detect_order_by_offset_overscan(cluster_id, stats),
    ]
