"""Read-only introspection of the agent's own memory tiers -- powers the
'Memory' page in the UI so operators can see what the agent has learned,
not just trust it blindly."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import MemoryTier

router = APIRouter()


@router.get("")
async def list_memory(
    tier: MemoryTier = Query(default=MemoryTier.EPISODIC),
    cluster_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> list[dict]:
    store = AgentMemoryStore.instance()
    return await store.list_recent(tier, str(cluster_id) if cluster_id else None, limit=limit)


@router.get("/search")
async def search_memory(q: str, limit: int = Query(default=8, le=50)) -> dict:
    store = AgentMemoryStore.instance()
    return await store.recall_all_tiers(q, limit_per_tier=limit)
