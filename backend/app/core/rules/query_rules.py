"""Query-shape findings. All of these are REQUIRES_CODE_CHANGE -- fixing a
query's own text (projection, predicates, pagination style, key lists) is an
application change the agent explains but never makes unilaterally."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from app.core.rules.base import (
    ClusterStats,
    elapsed_ms,
    group_by_normalized_statement,
    guess_target_keyspace,
    phase_count,
    phase_ms,
    service_ms,
    used_memory_bytes,
)
from app.models.enums import ActionType, FindingCategory, FindingSeverity
from app.models.schemas import Finding

LARGE_RESULT_BYTES = 5 * 1024 * 1024  # 5MB
HIGH_MEMORY_BYTES = 40 * 1024 * 1024  # 40MB per query
SLOW_PARSE_PLAN_MS = 50.0
SLOW_USE_KEYS_MS = 300.0
LARGE_USE_KEYS_COUNT = 50
CPU_BOUND_SERVICE_MS = 200.0
CPU_BOUND_MAX_RESULT_BYTES = 200 * 1024  # heavy compute over a small result, not a data-volume problem
CONCURRENT_WINDOW_S = 2.0
CONCURRENT_MIN_OVERLAP = 4

_SELECT_STAR_RE = re.compile(r"SELECT\s+(\*|`?\w+`?\.\*)", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_USE_KEYS_RE = re.compile(r"USE\s+KEYS\s*(\[[^\]]*\]|\$\w+)", re.IGNORECASE)
_JOIN_RE = re.compile(r"\bJOIN\b", re.IGNORECASE)
_LIKE_LEADING_WILDCARD_RE = re.compile(r"LIKE\s+['\"]%", re.IGNORECASE)
_CPU_HEAVY_FN_RE = re.compile(r"\b(REGEXP_CONTAINS|REGEXP_LIKE|REGEXP_REPLACE|ARRAY\s*\(|UPPER\(|LOWER\()", re.IGNORECASE)
_DDL_DML_RE = re.compile(r"^\s*(SELECT|EXPLAIN)\b", re.IGNORECASE)
_OFFSET_RE = re.compile(r"\bOFFSET\s+(\d+)", re.IGNORECASE)


def detect_large_result_sets(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    findings: list[Finding] = []
    groups = group_by_normalized_statement(stats.completed_requests)

    for normalized, rows in groups.items():
        sizes = [r.get("resultSize", 0) or 0 for r in rows]
        if not sizes:
            continue
        avg_size = sum(sizes) / len(sizes)
        if avg_size < LARGE_RESULT_BYTES or len(rows) < 2:
            continue

        severity = FindingSeverity.CRITICAL if avg_size > 3 * LARGE_RESULT_BYTES else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Query returns unusually large result payloads",
            description=(
                f"{len(rows)} executions of this query shape return an average of "
                f"{avg_size / (1024 * 1024):.1f}MB per response. Large payloads increase network I/O, "
                "client-side memory pressure, and time-to-first-byte for the application."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "avg_result_size_bytes": int(avg_size),
                "sample_statement": rows[0].get("statement"),
            },
            code_change_guidance=(
                "Add pagination (LIMIT/OFFSET or keyset pagination) and/or project only the fields the "
                "application actually uses instead of returning full documents. This requires changing the "
                "query text in the application, which the agent will not do on its own."
            ),
        ))
    return findings


def detect_select_star(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    findings: list[Finding] = []
    groups = group_by_normalized_statement(stats.completed_requests)

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        if not _SELECT_STAR_RE.search(sample_text):
            continue
        if len(rows) < 3:
            continue

        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.INFO,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Query selects entire documents instead of specific fields",
            description=(
                f"{len(rows)} executions of this query shape use `SELECT *` (or a full-document projection), "
                "which returns the entire document even when the application only needs a few fields. This "
                "also prevents the query planner from satisfying the query with a covering index."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "sample_statement": sample_text,
            },
            code_change_guidance=(
                "Change the SELECT clause to project only the fields the application reads. This can also "
                "let a covering secondary index serve the query without a document fetch."
            ),
        ))
    return findings


def detect_queueing_pressure(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """elapsedTime substantially exceeding serviceTime means requests are
    waiting -- for a query-service thread, for a lock, or in a socket queue
    -- rather than doing work. That's a capacity signal, not a query-rewrite
    signal, so it's evidence for the operator rather than something the
    agent proposes a specific fix for here."""
    slow_queue_rows = [
        r for r in stats.completed_requests
        if elapsed_ms(r) > 0 and (elapsed_ms(r) - service_ms(r)) > 500 and elapsed_ms(r) > 2 * max(service_ms(r), 1)
    ]
    total = len(stats.completed_requests)
    if total == 0 or len(slow_queue_rows) < max(5, total * 0.05):
        return []

    pct = round(100.0 * len(slow_queue_rows) / total, 1)
    return [Finding(
        cluster_id=cluster_id,
        category=FindingCategory.QUERY,
        severity=FindingSeverity.WARNING if pct < 20 else FindingSeverity.CRITICAL,
        action_type=ActionType.REQUIRES_CODE_CHANGE,
        title="Significant queueing delay between request arrival and execution",
        description=(
            f"{len(slow_queue_rows)} queries ({pct}% of those examined) show elapsed time well above service "
            "time, meaning requests are waiting rather than executing -- typically query-service thread "
            "saturation, lock contention, or a connection storm from the application."
        ),
        evidence={
            "affected_query_count": len(slow_queue_rows),
            "pct_of_all_queries": pct,
            "sample": slow_queue_rows[0].get("statement"),
        },
        code_change_guidance=(
            "This is a capacity/concurrency signal rather than a single query problem: check application "
            "connection pooling and concurrency against the query service's node count and CPU headroom "
            "before scaling the cluster."
        ),
    )]


def detect_high_memory_per_query(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """Distinct from resource_rules' bucket-level RAM-quota finding: this is
    per-query memory footprint (completed_requests.usedMemory) -- a single
    query shape that's memory-hungry (large sort/aggregation working set,
    huge intermediate arrays) even on an otherwise healthy cluster."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        mem_values = [used_memory_bytes(r) for r in rows if used_memory_bytes(r) > 0]
        if len(mem_values) < 2:
            continue
        avg_mem = sum(mem_values) / len(mem_values)
        if avg_mem < HIGH_MEMORY_BYTES:
            continue

        severity = FindingSeverity.CRITICAL if avg_mem > 3 * HIGH_MEMORY_BYTES else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.RESOURCE,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Query shape uses a large amount of query-engine memory",
            description=(
                f"{len(mem_values)} executions of this query shape use an average of "
                f"{avg_mem / (1024 * 1024):.1f}MB of query-engine memory (sort/aggregation/intermediate "
                "working set), which competes with every other concurrent query for the query service's "
                "memory quota."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(mem_values),
                "avg_used_memory_bytes": int(avg_mem),
                "sample_statement": rows[0].get("statement"),
            },
            code_change_guidance=(
                "Look for unindexed ORDER BY/GROUP BY forcing a full in-memory sort, or large intermediate "
                "arrays (UNNEST, ARRAY construction) that could be filtered earlier in the query. Adding a "
                "supporting index can also reduce this indirectly by avoiding an in-memory sort."
            ),
        ))
    return findings


def detect_high_cpu_service_time(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """completed_requests doesn't expose a literal OS kernel-time metric --
    this approximates CPU-bound queries as high serviceTime concentrated in
    expression evaluation rather than data movement: a small result payload
    (so it isn't the 'large result set' finding) combined with either a
    known CPU-heavy function in the statement (REGEXP_*, UPPER/LOWER over
    large text, ARRAY comprehensions) or serviceTime that's high without a
    correspondingly large result to justify it.

    Deliberately excludes statements that already have a more specific,
    better-targeted explanation elsewhere -- a real primaryScan/indexScan
    phase count means the time is I/O, not CPU (see index_rules); deep
    OFFSET and large USE KEYS lists have their own dedicated findings --
    otherwise the same slow query would get flagged three different ways
    for what's really one root cause."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        has_heavy_fn = bool(_CPU_HEAVY_FN_RE.search(sample_text))
        has_scan_io = any(phase_count(r, "primaryScan") or phase_count(r, "indexScan") for r in rows)
        has_own_finding = bool(_OFFSET_RE.search(sample_text) or _USE_KEYS_RE.search(sample_text))
        svc_values = [service_ms(r) for r in rows]
        avg_service = sum(svc_values) / len(svc_values) if svc_values else 0
        avg_result = sum((r.get("resultSize") or 0) for r in rows) / len(rows)

        if avg_service < CPU_BOUND_SERVICE_MS:
            continue
        if avg_result > CPU_BOUND_MAX_RESULT_BYTES and not has_heavy_fn:
            continue  # high service time here is more likely explained by data volume
        if (has_scan_io or has_own_finding) and not has_heavy_fn:
            continue  # a more specific rule already explains this query's cost
        if len(rows) < 3:
            continue

        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.WARNING if avg_service < 2 * CPU_BOUND_SERVICE_MS else FindingSeverity.CRITICAL,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Query spends significant CPU time evaluating expressions, not moving data",
            description=(
                f"{len(rows)} executions average {avg_service:.0f}ms of service time while returning only "
                f"~{avg_result / 1024:.0f}KB -- the time is going into expression evaluation "
                f"{'(the statement uses a CPU-intensive function like REGEXP_*/UPPER/LOWER/ARRAY over a large input)' if has_heavy_fn else 'rather than index/document I/O'}, "
                "which shows up as CPU/kernel time on the query-service node rather than as scan or fetch cost."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "avg_service_ms": round(avg_service, 1),
                "avg_result_bytes": int(avg_result),
                "uses_cpu_heavy_function": has_heavy_fn,
                "sample_statement": sample_text,
            },
            code_change_guidance=(
                "Move regex/string-transform work out of the WHERE/SELECT clause where possible (pre-compute "
                "and store a normalized field at write time instead of transforming it on every read), or "
                "narrow the input to the expensive function with a cheaper predicate first."
            ),
        ))
    return findings


def detect_slow_parse_plan(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """Ad-hoc statements (not prepared) pay parse+plan cost on every single
    execution. A long IN-list, deep OR chains, or a large literal payload in
    the statement text inflates that cost -- using a prepared statement or
    parameterizing the literals fixes it, which is an application change."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        parse_plan = [phase_ms(r, "parse") + phase_ms(r, "plan") for r in rows]
        parse_plan = [v for v in parse_plan if v > 0]
        if len(parse_plan) < 3:
            continue
        avg_pp = sum(parse_plan) / len(parse_plan)
        if avg_pp < SLOW_PARSE_PLAN_MS:
            continue

        sample_text = rows[0].get("statement") or ""
        is_prepared = bool(rows[0].get("preparedText"))
        severity = FindingSeverity.CRITICAL if avg_pp > 4 * SLOW_PARSE_PLAN_MS else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Slow parse/plan phase on a frequently repeated query shape",
            description=(
                f"{len(parse_plan)} executions of this query shape spend an average of {avg_pp:.0f}ms in "
                f"parse+plan alone, {'even though it appears to run as a prepared statement' if is_prepared else 'and it does not appear to run as a prepared statement'} "
                "-- a long literal list, many OR branches, or a large statement body all inflate this cost, "
                "and it's paid again on every execution for ad-hoc SQL."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(parse_plan),
                "avg_parse_plan_ms": round(avg_pp, 1),
                "is_prepared": is_prepared,
                "statement_length": len(sample_text),
                "sample_statement": sample_text[:400],
            },
            code_change_guidance=(
                "Use a prepared/parameterized statement instead of inlining literals (especially long "
                "IN-lists), so parse+plan happens once and is reused across executions."
            ),
        ))
    return findings


def detect_slow_use_keys(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """USE KEYS is normally the fast path (direct KV fetch, no index scan at
    all) -- when it's slow anyway, it's almost always because the key list
    itself is very large, which is a batching/application-shape issue."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        m = _USE_KEYS_RE.search(sample_text)
        if not m:
            continue
        avg_elapsed = sum(elapsed_ms(r) for r in rows) / len(rows)
        key_count = sample_text.count(",", m.start(), m.end()) + 1 if m.group(1).startswith("[") else None
        looks_large = (key_count is not None and key_count >= LARGE_USE_KEYS_COUNT)
        if avg_elapsed < SLOW_USE_KEYS_MS and not looks_large:
            continue
        if len(rows) < 2:
            continue

        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.WARNING,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Slow USE KEYS fetch" + (f" with a large key list (~{key_count} keys)" if looks_large else ""),
            description=(
                f"{len(rows)} executions of this USE KEYS query average {avg_elapsed:.0f}ms, which is slow "
                "for what's normally a direct key-value fetch with no index involved -- the usual cause is "
                "an oversized key list batched into a single request."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "avg_elapsed_ms": round(avg_elapsed, 1),
                "approx_key_count": key_count,
                "sample_statement": sample_text[:400],
            },
            code_change_guidance=(
                "Split very large USE KEYS lists into smaller batches (a few hundred keys at most), or use "
                "the SDK's bulk get API directly instead of routing a large key-value fetch through the "
                "query service."
            ),
        ))
    return findings


def detect_missing_where_clause(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """A SELECT with no WHERE at all reads the entire keyspace unconditionally
    -- distinct from the primary-scan finding (which is about an unindexed
    predicate); this is about there being no predicate whatsoever."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        if not _DDL_DML_RE.match(sample_text.strip()):
            continue
        if _WHERE_RE.search(sample_text) or _USE_KEYS_RE.search(sample_text):
            continue
        if len(rows) < 2:
            continue

        avg_result_count = sum((r.get("resultCount") or 0) for r in rows) / len(rows)
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.WARNING if avg_result_count > 100 else FindingSeverity.INFO,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Query has no WHERE clause -- reads the entire keyspace",
            description=(
                f"{len(rows)} executions of this query shape have no WHERE clause and no USE KEYS, returning "
                f"an average of {avg_result_count:.0f} documents each time with nothing to let the planner "
                "narrow the scan."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "avg_result_count": round(avg_result_count, 1),
                "sample_statement": sample_text,
            },
            code_change_guidance=(
                "Add a predicate that reflects what the application actually needs (a status filter, a time "
                "window, an owner/tenant id) -- an unconditional SELECT over a whole collection rarely "
                "reflects real intent and only gets more expensive as the collection grows."
            ),
        ))
    return findings


def detect_complex_joins(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """Each JOIN is effectively a nested-loop lookup (ON KEYS) or an index
    join in N1QL -- fan-out multiplies with every additional JOIN, and three
    or more in one statement is usually a sign the document model doesn't
    match the access pattern as well as embedding or denormalizing would."""
    JOIN_COUNT_WARN = 2
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        join_count = len(_JOIN_RE.findall(sample_text))
        if join_count < JOIN_COUNT_WARN:
            continue

        avg_elapsed = sum(elapsed_ms(r) for r in rows) / len(rows)
        severity = FindingSeverity.CRITICAL if join_count >= JOIN_COUNT_WARN + 1 else FindingSeverity.WARNING
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=severity,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title=f"Complex query joins {join_count} keyspaces together",
            description=(
                f"{len(rows)} executions of this query shape chain {join_count} JOINs, averaging "
                f"{avg_elapsed:.0f}ms elapsed. Each additional JOIN multiplies fan-out and adds a lookup "
                "stage, and N1QL JOINs perform best as direct ON KEYS lookups -- a hash/nested-loop join "
                "over a non-key predicate gets expensive fast."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "join_count": join_count,
                "avg_elapsed_ms": round(avg_elapsed, 1),
                "sample_statement": sample_text[:500],
            },
            code_change_guidance=(
                "Prefer ON KEYS joins (direct document-key lookups) over predicate-based joins where "
                "possible, or consider embedding frequently-joined data (e.g. denormalizing a customer name "
                "onto the order document) if this join runs on a hot path."
            ),
        ))
    return findings


def detect_ineffective_like(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """A LIKE pattern with a leading wildcard ('%term') can't use an index's
    sorted key prefix -- Couchbase has to fall back to scanning every
    candidate document/index entry and testing the pattern in-memory."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        sample_text = rows[0].get("statement") or ""
        if not _LIKE_LEADING_WILDCARD_RE.search(sample_text):
            continue
        if len(rows) < 2:
            continue

        avg_elapsed = sum(elapsed_ms(r) for r in rows) / len(rows)
        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.WARNING,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="LIKE pattern with a leading wildcard defeats index prefix matching",
            description=(
                f"{len(rows)} executions of this query shape use a LIKE pattern starting with `%`, averaging "
                f"{avg_elapsed:.0f}ms elapsed. A leading wildcard means no index can use its sorted key "
                "prefix to narrow the search, so every candidate has to be tested in-memory."
            ),
            evidence={
                "normalized_statement": normalized,
                "occurrence_count": len(rows),
                "avg_elapsed_ms": round(avg_elapsed, 1),
                "sample_statement": sample_text,
            },
            code_change_guidance=(
                "If this is substring/contains search, consider a Full-Text Search (FTS) index instead of "
                "LIKE -- FTS is built for this. If a prefix search would satisfy the actual requirement "
                "(`term%` instead of `%term%`), that alone lets a regular secondary index serve it."
            ),
        ))
    return findings


def detect_timeout_prone(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """Requests that hit the query timeout still land in completed_requests
    with state 'timeout' (or a non-zero errorCount) rather than 'completed'
    -- this groups those by shape so a query that's borderline-too-slow
    shows up before it becomes a customer-visible failure at scale."""
    groups = group_by_normalized_statement(stats.completed_requests)
    findings: list[Finding] = []

    for normalized, rows in groups.items():
        timed_out = [r for r in rows if (r.get("state") or "").lower() in ("timeout", "fatal") or (r.get("errorCount") or 0) > 0]
        if len(timed_out) < 2:
            continue
        pct = round(100.0 * len(timed_out) / len(rows), 1)

        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.CRITICAL if pct > 20 else FindingSeverity.WARNING,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title="Query shape times out or errors under load",
            description=(
                f"{len(timed_out)} of {len(rows)} executions ({pct}%) of this query shape ended in a timeout "
                "or error state. A query that's borderline-too-slow today becomes a reliability problem as "
                "data volume or concurrent load grows."
            ),
            evidence={
                "normalized_statement": normalized,
                "total_occurrences": len(rows),
                "timeout_or_error_count": len(timed_out),
                "pct_timeout_or_error": pct,
                "sample_statement": timed_out[0].get("statement"),
            },
            code_change_guidance=(
                "Treat this as a correctness issue, not just a performance one -- find and fix the underlying "
                "cost driver (missing index, unbounded result set, deep offset) rather than raising the "
                "client timeout, which just delays the failure."
            ),
        ))
    return findings


def detect_concurrent_conflicts(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    """Best-effort, timing-based signal: groups requests hitting the same
    keyspace into short time windows and flags windows with several
    overlapping requests where at least one errored -- consistent with
    write-write contention (CAS conflicts) or lock contention on hot
    documents under concurrent load. This is inferred from timing
    correlation, not a direct 'conflict' flag from Couchbase, so it's
    presented as a signal to investigate rather than a certainty."""
    by_keyspace: dict[str, list[dict]] = defaultdict(list)
    for row in stats.completed_requests:
        ks = guess_target_keyspace(row.get("statement") or "")
        if ks:
            by_keyspace[ks].append(row)

    findings: list[Finding] = []
    for keyspace, rows in by_keyspace.items():
        timestamped = []
        for r in rows:
            try:
                ts = datetime.fromisoformat((r.get("requestTime") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            timestamped.append((ts, r))
        timestamped.sort(key=lambda pair: pair[0])

        conflict_windows = 0
        errors_in_windows = 0
        i = 0
        while i < len(timestamped):
            window_start = timestamped[i][0]
            window = [timestamped[i]]
            j = i + 1
            while j < len(timestamped) and (timestamped[j][0] - window_start).total_seconds() <= CONCURRENT_WINDOW_S:
                window.append(timestamped[j])
                j += 1
            if len(window) >= CONCURRENT_MIN_OVERLAP:
                window_errors = sum(1 for _, r in window if (r.get("errorCount") or 0) > 0)
                if window_errors > 0:
                    conflict_windows += 1
                    errors_in_windows += window_errors
            i = j if j > i + 1 else i + 1

        if conflict_windows == 0:
            continue

        findings.append(Finding(
            cluster_id=cluster_id,
            category=FindingCategory.QUERY,
            severity=FindingSeverity.WARNING if conflict_windows < 3 else FindingSeverity.CRITICAL,
            action_type=ActionType.REQUIRES_CODE_CHANGE,
            title=f"Possible concurrent write conflicts on `{keyspace}`",
            description=(
                f"Found {conflict_windows} short time window(s) with {CONCURRENT_MIN_OVERLAP}+ overlapping "
                f"requests against `{keyspace}` where at least one request errored ({errors_in_windows} "
                "errors total) -- consistent with several clients racing to read-modify-write the same "
                "document(s) at once. This is inferred from request timing correlation, not a direct "
                "conflict flag, so treat it as a lead to investigate rather than a certainty."
            ),
            evidence={
                "keyspace": keyspace,
                "conflict_windows": conflict_windows,
                "errors_in_windows": errors_in_windows,
                "window_seconds": CONCURRENT_WINDOW_S,
            },
            code_change_guidance=(
                "Use CAS-checked replace operations with retry-on-conflict instead of blind upserts for "
                "hot documents, or route concurrent updates to the same document through a queue/single "
                "writer to avoid racing. Couchbase transactions are also an option if multiple documents "
                "need to change atomically together."
            ),
        ))
    return findings


def detect(cluster_id: UUID, stats: ClusterStats) -> list[Finding]:
    return [
        *detect_large_result_sets(cluster_id, stats),
        *detect_select_star(cluster_id, stats),
        *detect_queueing_pressure(cluster_id, stats),
        *detect_high_memory_per_query(cluster_id, stats),
        *detect_high_cpu_service_time(cluster_id, stats),
        *detect_slow_parse_plan(cluster_id, stats),
        *detect_slow_use_keys(cluster_id, stats),
        *detect_missing_where_clause(cluster_id, stats),
        *detect_complex_joins(cluster_id, stats),
        *detect_ineffective_like(cluster_id, stats),
        *detect_timeout_prone(cluster_id, stats),
        *detect_concurrent_conflicts(cluster_id, stats),
    ]
