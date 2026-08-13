"""Register/list/remove clusters under management, test connectivity, and
upload Couchbase support bundles as an offline alternative to a live
connection (see core/bundle_parser.py / bundle_client.py)."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import get_settings
from app.core.bundle_client import BundleClusterClient
from app.core.bundle_parser import BundleParseError, parse_support_bundle
from app.core.cluster_client import ClusterClient, ClusterUnreachableError
from app.core.store import StateStore
from app.models.enums import AccessMode, ClusterKind, ClusterSourceType, ClusterStatus
from app.models.schemas import Cluster, ClusterCreate, ClusterPublic

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=ClusterPublic)
async def register_cluster(payload: ClusterCreate) -> ClusterPublic:
    cluster = Cluster(**payload.model_dump())
    client = ClusterClient(cluster)
    try:
        info = await client.test_connection()
        cluster.status = ClusterStatus.CONNECTED
        logger.info("Registered cluster '%s': %s", cluster.name, info)
    except ClusterUnreachableError as exc:
        cluster.status = ClusterStatus.UNREACHABLE
        logger.warning("Cluster '%s' registered but unreachable: %s", cluster.name, exc)

    if cluster.status == ClusterStatus.CONNECTED:
        try:
            cluster.granted_roles, cluster.access_mode_note = await client.fetch_granted_roles()
        except Exception as exc:  # noqa: BLE001
            logger.info("Role check failed for '%s' (non-fatal): %s", cluster.name, exc)
    client.close()

    await StateStore.instance().save_cluster(cluster)
    return cluster.safe()


@router.post("/upload-bundle", response_model=ClusterPublic)
async def upload_support_bundle(
    name: str = Form(...),
    kind: ClusterKind = Form(ClusterKind.ENTERPRISE),
    file: UploadFile = File(...),
) -> ClusterPublic:
    """Registers a cluster backed by a static, offline Couchbase support
    bundle instead of a live connection -- for demos/analysis when you have
    a cbcollect_info archive but no reachable cluster. Always read-only:
    there's nothing live to apply a change to. See core/bundle_parser.py's
    module docstring for exactly what is and isn't extracted."""
    settings = get_settings()
    max_bytes = settings.bundle_max_size_mb * 1024 * 1024

    cluster_id = uuid4()
    upload_dir = Path(settings.bundle_storage_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = "".join(Path(file.filename or "bundle.zip").suffixes)[-20:] or ".zip"
    archive_path = upload_dir / f"{cluster_id}{suffix}"

    written = 0
    try:
        with open(archive_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        413, f"Support bundle exceeds the {settings.bundle_max_size_mb}MB upload limit."
                    )
                out.write(chunk)
    except HTTPException:
        archive_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if written == 0:
        archive_path.unlink(missing_ok=True)
        raise HTTPException(400, "Uploaded file is empty.")

    extract_dir = Path(tempfile.mkdtemp(prefix=f"bundle-{cluster_id}-"))
    try:
        snapshot = parse_support_bundle(archive_path, extract_dir)
    except BundleParseError as exc:
        archive_path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    snapshots_dir = Path(settings.bundle_storage_dir) / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    (snapshots_dir / f"{cluster_id}.json").write_text(
        json.dumps({k: v for k, v in snapshot.items() if k != "note"}, default=str)
    )

    has_data = bool(snapshot["completed_requests"] or snapshot["index_catalog"] or snapshot["resource_stats"])
    cluster = Cluster(
        cluster_id=cluster_id,
        name=name,
        kind=kind,
        connection_string=f"support-bundle://{file.filename or archive_path.name}",
        username="",
        password="",
        access_mode=AccessMode.READ_ONLY,  # forced -- see ClusterSourceType.SUPPORT_BUNDLE docstring
        source_type=ClusterSourceType.SUPPORT_BUNDLE,
        bundle_filename=file.filename or archive_path.name,
        bundle_uploaded_at=datetime.utcnow(),
        bundle_parse_note=snapshot["note"],
        status=ClusterStatus.CONNECTED if has_data else ClusterStatus.UNREACHABLE,
    )
    await StateStore.instance().save_cluster(cluster)
    logger.info("Registered support-bundle cluster '%s': %s", cluster.name, snapshot["note"])
    return cluster.safe()


@router.get("", response_model=list[ClusterPublic])
async def list_clusters() -> list[ClusterPublic]:
    clusters = await StateStore.instance().list_clusters()
    return [c.safe() for c in clusters]


@router.get("/{cluster_id}", response_model=ClusterPublic)
async def get_cluster(cluster_id: UUID) -> ClusterPublic:
    cluster = await StateStore.instance().get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")
    return cluster.safe()


@router.post("/{cluster_id}/test-connection", response_model=ClusterPublic)
async def test_connection(cluster_id: UUID) -> ClusterPublic:
    cluster = await StateStore.instance().get_cluster(cluster_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    if cluster.source_type == ClusterSourceType.SUPPORT_BUNDLE:
        # Nothing to reconnect to -- just confirm the cached snapshot parsed
        # at upload time is still readable, rather than attempting (and
        # failing) a live SDK connection against a placeholder connection
        # string.
        try:
            await BundleClusterClient(cluster).test_connection()
            cluster.status = ClusterStatus.CONNECTED
        except Exception as exc:  # noqa: BLE001
            cluster.status = ClusterStatus.UNREACHABLE
            logger.info("Bundle snapshot check failed for '%s': %s", cluster.name, exc)
        await StateStore.instance().save_cluster(cluster)
        return cluster.safe()

    client = ClusterClient(cluster)
    try:
        await client.test_connection()
        cluster.status = ClusterStatus.CONNECTED
    except ClusterUnreachableError:
        cluster.status = ClusterStatus.UNREACHABLE

    if cluster.status == ClusterStatus.CONNECTED:
        try:
            cluster.granted_roles, cluster.access_mode_note = await client.fetch_granted_roles()
        except Exception as exc:  # noqa: BLE001
            logger.info("Role check failed for '%s' (non-fatal): %s", cluster.name, exc)
    client.close()

    await StateStore.instance().save_cluster(cluster)
    return cluster.safe()


@router.delete("/{cluster_id}")
async def delete_cluster(cluster_id: UUID) -> dict:
    cluster = await StateStore.instance().get_cluster(cluster_id)
    await StateStore.instance().delete_cluster(cluster_id)
    if cluster and cluster.source_type == ClusterSourceType.SUPPORT_BUNDLE:
        settings = get_settings()
        snapshot = Path(settings.bundle_storage_dir) / "snapshots" / f"{cluster_id}.json"
        snapshot.unlink(missing_ok=True)
        for archive in (Path(settings.bundle_storage_dir) / "uploads").glob(f"{cluster_id}.*"):
            archive.unlink(missing_ok=True)
    return {"deleted": str(cluster_id)}
