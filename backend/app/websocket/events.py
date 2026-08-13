"""In-process pub/sub fan-out to connected WebSocket clients -- new findings
and analysis-run summaries are pushed live so the dashboard and the agent
chat panel can both react without polling."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_connections: set[WebSocket] = set()
_lock = asyncio.Lock()


async def register(ws: WebSocket) -> None:
    await ws.accept()
    async with _lock:
        _connections.add(ws)


async def unregister(ws: WebSocket) -> None:
    async with _lock:
        _connections.discard(ws)


async def broadcast(event_type: str, payload: dict[str, Any]) -> None:
    message = json.dumps({"type": event_type, "payload": payload}, default=str)
    async with _lock:
        dead = []
        for ws in _connections:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            _connections.discard(ws)


def broadcast_sync(event_type: str, payload: dict[str, Any]) -> None:
    """Callback-friendly wrapper for code that isn't already in an event loop
    context (e.g. the analyzer's on_progress callback)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(broadcast(event_type, payload))
        else:
            loop.run_until_complete(broadcast(event_type, payload))
    except RuntimeError:
        logger.debug("No running event loop to broadcast '%s' on.", event_type)
