"""Agent memory store backed by Couchbase Enterprise Edition (or Capella),
using native Couchbase Vector Search (FTS) for recall, across three tiers:

  - short_term: rolling working context for whatever analysis/chat session is
    in flight right now. Documents carry a server-side maxTTL (set on the
    collection by scripts/init_memory.py) so this tier self-prunes.
  - episodic:   one durable document per discrete event -- a finding raised,
    an optimization applied, an approval/rejection, a chat exchange, a
    sandbox test result. This is the agent's diary.
  - long_term:  consolidated knowledge distilled from episodic memory -- a
    cluster's normal query-latency baseline, "this team always rejects
    primary-index suggestions on bucket X", recurring bottleneck patterns.
    Written by memory/consolidation.py, read far more than it's written.

Each tier is its own Couchbase collection with its own FTS vector index
(agent_memory_<tier>_idx). recall() runs a native ANN vector query; if that
index is momentarily unavailable it falls back to a brute-force N1QL +
cosine-similarity scan over the same embeddings, purely as a resilience net.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, SearchOptions
from couchbase.vector_search import VectorQuery, VectorSearch

from app.config import get_settings
from app.memory.embeddings import embed_text, summarize_for_embedding
from app.models.enums import MemoryTier

logger = logging.getLogger(__name__)

FALLBACK_SCAN_LIMIT = 500


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class AgentMemoryStore:
    """Singleton-ish accessor for the agent's short-term/episodic/long-term memory."""

    _instance: "AgentMemoryStore | None" = None

    def __init__(self) -> None:
        self.settings = get_settings()
        self._cluster: Cluster | None = None
        self._vector_search_unavailable: set[str] = set()

    @classmethod
    def instance(cls) -> "AgentMemoryStore":
        if cls._instance is None:
            cls._instance = AgentMemoryStore()
        return cls._instance

    def _connect(self) -> Cluster:
        if self._cluster is not None:
            return self._cluster
        auth = PasswordAuthenticator(self.settings.memory_cb_username, self.settings.memory_cb_password)
        cluster = Cluster(self.settings.memory_cb_connection_string, ClusterOptions(auth))
        # wait_until_ready requires a timedelta, not a bare int -- see the
        # identical fix/comment in core/cluster_client.py._connect().
        cluster.wait_until_ready(timeout=timedelta(seconds=15))
        self._cluster = cluster
        return cluster

    def _collection(self, tier: MemoryTier):
        cluster = self._connect()
        bucket = cluster.bucket(self.settings.memory_cb_bucket)
        scope = bucket.scope(self.settings.memory_cb_scope)
        return scope.collection(tier.value)

    def _keyspace(self, tier: MemoryTier) -> str:
        return f"`{self.settings.memory_cb_bucket}`.`{self.settings.memory_cb_scope}`.`{tier.value}`"

    def _index_name(self, tier: MemoryTier) -> str:
        return f"agent_memory_{tier.value}_idx"

    # -- write -------------------------------------------------------------

    async def remember(
        self, tier: MemoryTier, kind: str, payload: dict[str, Any], cluster_id: str | None = None
    ) -> str:
        """Embed and persist a memory event in the given tier. Returns the doc id."""
        text = summarize_for_embedding(kind, payload)
        try:
            vector = await embed_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding failed, storing memory without vector: %s", exc)
            vector = []

        doc_id = f"mem::{tier.value}::{uuid4()}"
        collection = self._collection(tier)
        collection.upsert(doc_id, {
            "kind": kind,
            "cluster_id": cluster_id,
            "text": text,
            "payload": payload,
            "embedding": vector,
            "created_at": datetime.utcnow().isoformat(),
        })
        return doc_id

    # -- read ----------------------------------------------------------------

    async def recall(self, tier: MemoryTier, query_text: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            query_vector = await embed_text(query_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding failed for recall query, returning no memories: %s", exc)
            return []

        if tier.value not in self._vector_search_unavailable:
            try:
                return self._recall_via_vector_search(tier, query_vector, limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Vector search recall failed for tier '%s' (%s) -- switching to N1QL "
                    "cosine-similarity fallback until the FTS index becomes available.", tier.value, exc,
                )
                self._vector_search_unavailable.add(tier.value)

        try:
            return self._recall_via_cosine_fallback(tier, query_vector, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cosine-similarity fallback recall failed for tier '%s': %s", tier.value, exc)
            return []

    async def recall_all_tiers(
        self, query_text: str, limit_per_tier: int = 4
    ) -> dict[str, list[dict[str, Any]]]:
        """Recall across all three tiers -- used to ground chat answers and new
        analysis passes in the fullest available context."""
        results: dict[str, list[dict[str, Any]]] = {}
        for tier in (MemoryTier.LONG_TERM, MemoryTier.EPISODIC, MemoryTier.SHORT_TERM):
            results[tier.value] = await self.recall(tier, query_text, limit_per_tier)
        return results

    def _recall_via_vector_search(
        self, tier: MemoryTier, query_vector: list[float], limit: int
    ) -> list[dict[str, Any]]:
        cluster = self._connect()
        request = VectorSearch.from_vector_query(
            VectorQuery("embedding", query_vector, num_candidates=max(limit * 4, 20))
        )
        result = cluster.search(
            self._index_name(tier),
            request,
            SearchOptions(limit=limit, fields=["kind", "cluster_id", "text", "payload", "created_at"]),
        )
        memories = []
        for row in result.rows():
            memories.append({"id": row.id, "score": row.score, "tier": tier.value, **(row.fields or {})})
        return memories

    def _recall_via_cosine_fallback(
        self, tier: MemoryTier, query_vector: list[float], limit: int
    ) -> list[dict[str, Any]]:
        cluster = self._connect()
        query = (
            f"SELECT META(m).id AS id, m.kind, m.cluster_id, m.text, m.payload, "
            f"m.embedding, m.created_at FROM {self._keyspace(tier)} AS m "
            f"WHERE m.embedding IS NOT MISSING AND ARRAY_LENGTH(m.embedding) > 0 "
            f"ORDER BY m.created_at DESC LIMIT {FALLBACK_SCAN_LIMIT}"
        )
        result = cluster.query(query)

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in result:
            score = _cosine_similarity(query_vector, row.get("embedding") or [])
            scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        memories = []
        for score, row in scored[:limit]:
            row = dict(row)
            row.pop("embedding", None)
            row["score"] = score
            row["tier"] = tier.value
            memories.append(row)
        return memories

    async def list_recent(self, tier: MemoryTier, cluster_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
        cluster = self._connect()
        where_cluster = f"AND m.cluster_id = '{cluster_id}'" if cluster_id else ""
        query = (
            f"SELECT META(m).id AS id, m.kind, m.cluster_id, m.text, m.payload, m.created_at "
            f"FROM {self._keyspace(tier)} AS m WHERE 1=1 {where_cluster} "
            f"ORDER BY m.created_at DESC LIMIT {limit}"
        )
        result = cluster.query(query)
        return [dict(row) | {"tier": tier.value} for row in result]

    def close(self) -> None:
        if self._cluster is not None:
            self._cluster.close()
            self._cluster = None
