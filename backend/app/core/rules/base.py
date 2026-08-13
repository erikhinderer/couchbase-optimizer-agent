"""Shared helpers for rule modules: normalizing completed_requests statements
into groups, and a couple of small text heuristics used by more than one
rule. Every rule module exports a `detect(cluster_id, stats) -> list[Finding]`
function; core/analyzer.py collects and dedupes their output."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"|\$\w+|\b\d+\b")
_WHERE_EQ_RE = re.compile(r"WHERE\s+`?([a-zA-Z_][\w.]*)`?\s*=\s*", re.IGNORECASE)
_FROM_RE = re.compile(r"FROM\s+`?([a-zA-Z_][\w-]*)`?", re.IGNORECASE)
# UPDATE/INSERT INTO/MERGE INTO name their target keyspace differently than a
# SELECT's FROM clause -- needed so DML statements (e.g. the hot-document
# writes concurrent_conflicts looks for) also resolve to a keyspace.
_DML_KEYSPACE_RE = re.compile(r"(?:UPDATE|INTO)\s+`?([a-zA-Z_][\w-]*)`?", re.IGNORECASE)


@dataclass
class ClusterStats:
    """Everything a rule needs, gathered once per analysis pass."""

    completed_requests: list[dict[str, Any]] = field(default_factory=list)
    index_catalog: list[dict[str, Any]] = field(default_factory=list)
    resource_stats: dict[str, Any] = field(default_factory=dict)
    bucket_names: list[str] = field(default_factory=list)


def normalize_statement(statement: str) -> str:
    """Collapses literal values so repeated executions of the same query
    shape group together, mirroring how completed_requests analysis tools
    bucket queries by 'normalized statement' rather than raw text."""
    return _LITERAL_RE.sub("?", statement or "").strip()


def group_by_normalized_statement(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[normalize_statement(row.get("statement", ""))].append(row)
    return groups


def guess_target_keyspace(statement: str) -> str | None:
    text = statement or ""
    m = _FROM_RE.search(text)
    if m:
        return m.group(1)
    m = _DML_KEYSPACE_RE.search(text)
    return m.group(1) if m else None


def guess_simple_equality_field(statement: str) -> str | None:
    """Very conservative: only returns a field name when the statement has a
    single, simple `WHERE field = ...` predicate we're confident an index on
    that field would serve -- anything more complex (OR, function calls,
    nested paths beyond one level, array predicates) is left for a human to
    design an index for, and the finding is classified accordingly."""
    m = _WHERE_EQ_RE.search(statement or "")
    if not m:
        return None
    field_name = m.group(1)
    if any(tok in field_name for tok in ("(", ")", "[", "]")):
        return None
    return field_name


def elapsed_ms(row: dict[str, Any]) -> float:
    return _duration_to_ms(row.get("elapsedTime"))


def service_ms(row: dict[str, Any]) -> float:
    return _duration_to_ms(row.get("serviceTime"))


def phase_ms(row: dict[str, Any], phase: str) -> float:
    """completed_requests' `phaseTimes` object holds one Go-duration string
    per execution phase (authorize, parse, plan, run, ...) -- this is the
    only place those individual phase costs are exposed, distinct from the
    request-level elapsedTime/serviceTime totals."""
    return _duration_to_ms((row.get("phaseTimes") or {}).get(phase))


def phase_count(row: dict[str, Any], phase: str) -> int:
    return int((row.get("phaseCounts") or {}).get(phase) or 0)


def used_memory_bytes(row: dict[str, Any]) -> int:
    return int(row.get("usedMemory") or 0)


def _duration_to_ms(value: Any) -> float:
    """completed_requests durations come back as Go-style strings like
    '1.863s' or '840.75ms'."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        if s.endswith("ms"):
            return float(s[:-2])
        if s.endswith("us") or s.endswith("µs"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1]) * 1000.0
        return float(s)
    except ValueError:
        return 0.0
