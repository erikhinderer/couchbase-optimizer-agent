"""Promotes episodic memory into long-term memory.

Short-term memory is the agent's scratchpad while it's actively working
(mid-analysis, mid-conversation) and self-expires via the collection's maxTTL.
Episodic memory is the durable, one-event-per-document diary. Long-term memory
is neither of those -- it's a small number of consolidated, LLM-synthesized
summaries per cluster (baselines, recurring patterns, standing lessons) that
ground future analysis and chat answers without having to re-read the entire
episodic history every time.

This runs periodically per cluster (see core/scheduler.py) rather than on
every single episodic write, so long-term memory stays a distillation instead
of growing as fast as episodic memory does.
"""
from __future__ import annotations

import logging

from app.core.llm_client import LLMClient
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import MemoryTier

logger = logging.getLogger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """You are the long-term memory consolidator for the Couchbase \
Optimizer Agent. You will be given a list of recent episodic memory entries (findings raised, \
optimizations applied, approvals/rejections, sandbox test results) for one Couchbase cluster. \
Write a short, dense summary (6-10 bullet lines max) capturing: recurring problem patterns, \
what has already been fixed, what the operator team tends to reject or approve, and any \
performance baseline worth remembering (typical query latency, index usage mix). Do not \
restate every event -- distill. Output plain text bullet lines only, no preamble."""


async def consolidate_long_term_memory(cluster_id: str, lookback: int = 40) -> str | None:
    store = AgentMemoryStore.instance()
    episodes = await store.list_recent(MemoryTier.EPISODIC, cluster_id, limit=lookback)
    if not episodes:
        return None

    lines = [f"- [{e.get('created_at', '')}] {e.get('kind', '')}: {e.get('text', '')}" for e in episodes]
    user_prompt = "Recent episodic memory for cluster {}:\n{}".format(cluster_id, "\n".join(lines))

    try:
        summary = await LLMClient().chat(CONSOLIDATION_SYSTEM_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Long-term memory consolidation failed for cluster %s: %s", cluster_id, exc)
        return None

    if not summary:
        return None

    await store.remember(
        MemoryTier.LONG_TERM,
        "consolidated_summary",
        {"summary": summary, "source_episode_count": len(episodes)},
        cluster_id=cluster_id,
    )
    return summary
