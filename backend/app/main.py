"""FastAPI app entrypoint: wires routers, CORS, the WebSocket event stream,
and starts the continuous-analysis scheduler on boot. Also seeds one cluster
from env vars on first startup if SEED_CLUSTER_* are set, purely for
convenience getting a fresh `docker compose up` to show data immediately."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, chat, clusters, memory, optimizations
from app.config import get_settings
from app.core.cluster_client import ClusterClient, ClusterUnreachableError
from app.core.scheduler import ContinuousAnalysisScheduler
from app.core.store import StateStore
from app.models.enums import ClusterKind, ClusterStatus
from app.models.schemas import Cluster
from app.websocket.events import register, unregister

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
scheduler = ContinuousAnalysisScheduler()


async def _seed_cluster_from_env() -> None:
    import os

    name = os.environ.get("SEED_CLUSTER_NAME")
    if not name:
        return
    store = StateStore.instance()
    existing = [c for c in await store.list_clusters() if c.name == name]
    if existing:
        return

    cluster = Cluster(
        name=name,
        kind=ClusterKind(os.environ.get("SEED_CLUSTER_KIND", "enterprise")),
        connection_string=os.environ.get("SEED_CLUSTER_CONNECTION_STRING", ""),
        username=os.environ.get("SEED_CLUSTER_USERNAME", ""),
        password=os.environ.get("SEED_CLUSTER_PASSWORD", ""),
    )
    if not cluster.connection_string:
        return

    client = ClusterClient(cluster)
    try:
        await client.test_connection()
        cluster.status = ClusterStatus.CONNECTED
    except ClusterUnreachableError as exc:
        cluster.status = ClusterStatus.UNREACHABLE
        logger.warning("Seed cluster '%s' unreachable at boot: %s", name, exc)
    finally:
        client.close()

    await store.save_cluster(cluster)
    logger.info("Seeded cluster '%s' from environment.", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _seed_cluster_from_env()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clusters.router, prefix="/api/clusters", tags=["clusters"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(optimizations.router, prefix="/api/findings", tags=["findings"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(memory.router, prefix="/api/memory", tags=["memory"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await register(ws)
    try:
        while True:
            await ws.receive_text()  # client doesn't send anything meaningful; just keep the socket open
    except WebSocketDisconnect:
        pass
    finally:
        await unregister(ws)
