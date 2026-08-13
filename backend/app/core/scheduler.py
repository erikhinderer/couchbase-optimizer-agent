"""Background loop: continuously (re-)analyzes every registered cluster on a
fixed interval, and periodically consolidates episodic memory into long-term
memory per cluster. Started once from main.py's lifespan handler.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.core.analyzer import run_analysis
from app.core.store import StateStore
from app.memory.consolidation import consolidate_long_term_memory
from app.websocket.events import broadcast_sync

logger = logging.getLogger(__name__)

_CONSOLIDATE_EVERY_N_PASSES = 6


class ContinuousAnalysisScheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._task: asyncio.Task | None = None
        self._pass_counts: dict[str, int] = {}

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("Continuous analysis scheduler started (interval=%ss).", self.settings.analysis_interval_s)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        # Shared singleton -- see the comment in analyzer.py's run_analysis()
        # for why a bare StateStore() here silently disconnected the
        # scheduler's writes from what the API routes could see.
        store = StateStore.instance()
        while True:
            try:
                clusters = await store.list_clusters()
                for cluster in clusters:
                    try:
                        summary = await run_analysis(
                            cluster, on_progress=lambda t, p: broadcast_sync(t, p)
                        )
                        broadcast_sync("analysis_complete", summary.model_dump(mode="json"))

                        cid = str(cluster.cluster_id)
                        self._pass_counts[cid] = self._pass_counts.get(cid, 0) + 1
                        if self._pass_counts[cid] % _CONSOLIDATE_EVERY_N_PASSES == 0:
                            await consolidate_long_term_memory(cid)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Analysis pass failed for cluster %s: %s", cluster.name, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Scheduler loop error: %s", exc)

            await asyncio.sleep(self.settings.analysis_interval_s)
