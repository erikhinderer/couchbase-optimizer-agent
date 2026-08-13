"""
Best-effort extraction of a Couchbase support bundle (cbcollect_info output,
a .zip or .tar.gz/.tgz archive) into the same shape core/rules/base.py's
ClusterStats expects, so the exact same rule engine that runs against a live
cluster can also run against an offline bundle -- see bundle_client.py for
the read side of this.

What this does NOT do: implement a full, version-pinned cbcollect_info
schema parser. Support-bundle contents vary by Couchbase version and by the
collection profile the operator chose (default vs full), and there is no
single stable file that always contains a `system:completed_requests` /
`system:indexes` export -- those only end up in the bundle if the operator's
Couchbase version/profile happened to capture them, or if they were added to
the bundle manually before upload. So instead of guessing an undocumented
per-version file layout, this scans every JSON file and every .log/.txt file
in the bundle and picks out anything that *structurally* matches what the
rule engine consumes:

  - a list of dicts with a "statement" field            -> completed_requests row
  - a list of dicts with "keyspace_id"/"is_primary"/"using" -> system:indexes row
  - a dict shaped like the /pools/default response        -> node stats
  - a list of dicts shaped like /pools/default/buckets     -> bucket stats

This means: a bundle that includes an admin-exported snapshot of those
system keyspaces (many teams capture one before opening a support ticket) or
diag.log's embedded REST dumps (which include /pools/default and
/pools/default/buckets by default in every bundle) will produce real
findings. A bundle that only has server logs and core dumps will parse to
mostly-empty stats and the caller should say so plainly -- see `note` in the
returned snapshot, which is always populated and shown to the user.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tarfile
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Guards against pathological bundles (huge log dumps, core files) blowing up
# scan time/memory -- a support bundle can be several GB; we only need the
# small structured-data needles within it, not the full haystack.
_MAX_FILES_TO_SCAN = 8000
_MAX_FILE_BYTES_TO_SCAN = 25 * 1024 * 1024
_SCANNABLE_SUFFIXES = (".json", ".log", ".txt")
_SECTION_SPLIT_RE = re.compile(r"\n-{10,}[^\n]*\n|\n={10,}[^\n]*\n")


class BundleParseError(Exception):
    pass


def _archive_kind(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar.gz", ".tgz", ".tar")):
        return "tar"
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return None
    if head[:2] == b"PK":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tar"
    return None


def _resolve_safe(dest: Path, member_path: str) -> Path | None:
    """Guards against zip-slip / tar-slip: refuses any member whose resolved
    path would land outside `dest`, regardless of '..' segments, absolute
    paths, or symlink tricks in the member name itself."""
    dest_resolved = dest.resolve()
    target = (dest / member_path).resolve()
    if target != dest_resolved and dest_resolved not in target.parents:
        return None
    return target


def _safe_extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            target = _resolve_safe(dest, member.filename)
            if target is None:
                logger.warning("Skipping unsafe zip member: %s", member.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    mode = "r:gz" if archive.name.endswith((".gz", ".tgz")) else "r:"
    with tarfile.open(archive, mode) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            target = _resolve_safe(dest, member.name)
            if target is None:
                logger.warning("Skipping unsafe tar member: %s", member.name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with open(target, "wb") as dst:
                dst.write(src.read())


def _classify(
    data: Any,
    completed_requests: list[dict],
    index_catalog: list[dict],
    node_stats: list[dict],
    bucket_stats: list[dict],
) -> bool:
    """Returns True if `data` matched a known shape and was recorded."""
    if isinstance(data, list):
        if not data or not isinstance(data[0], dict):
            return False
        sample = data[0]
        if "statement" in sample and ("elapsedTime" in sample or "requestTime" in sample or "resultCount" in sample):
            completed_requests.extend(r for r in data if isinstance(r, dict) and "statement" in r)
            return True
        if "name" in sample and ("keyspace_id" in sample or "is_primary" in sample or "using" in sample):
            index_catalog.extend(r for r in data if isinstance(r, dict))
            return True
        if "name" in sample and "basicStats" in sample:
            bucket_stats.extend(
                {
                    "name": b.get("name"),
                    "ram_quota_mb": ((b.get("quota") or {}).get("ram", 0) or 0) // (1024 * 1024),
                    "basic_stats": b.get("basicStats", {}),
                }
                for b in data
                if isinstance(b, dict)
            )
            return True
        return False

    if isinstance(data, dict):
        nodes = data.get("nodes")
        if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict) and "systemStats" in nodes[0]:
            node_stats.extend(
                {
                    "hostname": n.get("hostname"),
                    "cpu_utilization_rate": (n.get("systemStats") or {}).get("cpu_utilization_rate"),
                    "mem_free": (n.get("systemStats") or {}).get("mem_free"),
                    "mem_total": (n.get("systemStats") or {}).get("mem_total"),
                    "status": n.get("status"),
                }
                for n in nodes
                if isinstance(n, dict)
            )
            return True
        # N1QL response envelope: {"requestID": ..., "results": [...]}
        if isinstance(data.get("results"), list):
            return _classify(data["results"], completed_requests, index_catalog, node_stats, bucket_stats)
        # {"indexes": [...]} style export
        if isinstance(data.get("indexes"), list):
            return _classify(data["indexes"], completed_requests, index_catalog, node_stats, bucket_stats)
        return False

    return False


def _scan_log_file(
    fpath: Path,
    completed_requests: list[dict],
    index_catalog: list[dict],
    node_stats: list[dict],
    bucket_stats: list[dict],
) -> int:
    """Best-effort text scan for two patterns real Couchbase logs produce:
    one-JSON-object-per-line request logging, and diag.log's section-
    delimited pretty-printed REST dumps (every bundle includes a
    /pools/default and /pools/default/buckets dump this way by default)."""
    try:
        text = fpath.read_text(errors="ignore")
    except OSError:
        return 0

    matched = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or "{" not in line or not line.endswith("}"):
            continue
        obj_start = line.find("{")
        try:
            obj = json.loads(line[obj_start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "statement" in obj:
            completed_requests.append(obj)
            matched += 1

    for block in _SECTION_SPLIT_RE.split(text):
        block = block.strip()
        if not block or block[0] not in "{[":
            continue
        end_char = "}" if block[0] == "{" else "]"
        last = block.rfind(end_char)
        if last == -1:
            continue
        try:
            data = json.loads(block[: last + 1])
        except json.JSONDecodeError:
            continue
        if _classify(data, completed_requests, index_catalog, node_stats, bucket_stats):
            matched += 1

    return matched


def parse_support_bundle(archive_path: Path, extract_dir: Path) -> dict[str, Any]:
    """Extracts `archive_path` into `extract_dir` and scans it. Returns a
    dict with completed_requests/index_catalog/resource_stats/bucket_names
    (the same fields core/rules/base.py's ClusterStats holds) plus a `note`
    string summarizing what was and wasn't found -- always populated, shown
    to the user regardless of outcome so an empty result is legible rather
    than a silent no-op."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    kind = _archive_kind(archive_path)
    if kind == "zip":
        _safe_extract_zip(archive_path, extract_dir)
    elif kind == "tar":
        _safe_extract_tar(archive_path, extract_dir)
    else:
        raise BundleParseError(
            "Unrecognized archive format -- expected a .zip, .tar.gz, or .tgz file "
            "(the formats `cbcollect_info` produces)."
        )

    completed_requests: list[dict] = []
    index_catalog: list[dict] = []
    node_stats: list[dict] = []
    bucket_stats: list[dict] = []
    files_scanned = 0
    json_files_parsed = 0

    for root, _dirs, files in os.walk(extract_dir):
        for fname in files:
            if files_scanned >= _MAX_FILES_TO_SCAN:
                break
            if not fname.lower().endswith(_SCANNABLE_SUFFIXES):
                continue
            fpath = Path(root) / fname
            files_scanned += 1
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            if size == 0 or size > _MAX_FILE_BYTES_TO_SCAN:
                continue

            if fname.lower().endswith(".json"):
                try:
                    data = json.loads(fpath.read_text(errors="ignore"))
                except (json.JSONDecodeError, OSError):
                    continue
                json_files_parsed += 1
                _classify(data, completed_requests, index_catalog, node_stats, bucket_stats)
            else:
                _scan_log_file(fpath, completed_requests, index_catalog, node_stats, bucket_stats)
        if files_scanned >= _MAX_FILES_TO_SCAN:
            break

    bucket_names = sorted({b.get("name") for b in bucket_stats if b.get("name")})
    if not bucket_names:
        bucket_names = sorted(
            {r.get("keyspace_id") for r in (completed_requests + index_catalog) if r.get("keyspace_id")}
        )

    resource_stats: dict[str, Any] = {}
    if node_stats:
        resource_stats["nodes"] = node_stats
    if bucket_stats:
        resource_stats["buckets"] = bucket_stats

    note = (
        f"Scanned {files_scanned} file(s) ({json_files_parsed} JSON) in the bundle. Found "
        f"{len(completed_requests)} query-log row(s), {len(index_catalog)} index definition(s), "
        f"{len(node_stats)} node stat snapshot(s), {len(bucket_stats)} bucket stat snapshot(s)."
    )
    if not completed_requests and not index_catalog:
        note += (
            " No system:completed_requests- or system:indexes-shaped data was found -- this bundle's "
            "Couchbase version/collection profile may not capture query-service diagnostics, so "
            "index/query findings will likely be empty. Resource findings may still work if node/bucket "
            "stats were found."
        )

    return {
        "completed_requests": completed_requests,
        "index_catalog": index_catalog,
        "resource_stats": resource_stats,
        "bucket_names": bucket_names,
        "note": note,
    }
