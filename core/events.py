"""core/events.py — In-process event bus for SSE push

Single asyncio Queue per client. Publishers put events; the SSE endpoint
drains the queue and streams JSON events to browsers.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from core.logger import get_logger

log = get_logger("events")


class EventBus:
    """Simple async pub/sub bus for in-process SSE."""

    def __init__(self) -> None:
        self._clients: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        """Create a new client queue and return it."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._clients.append(q)
        log.debug(f"SSE client subscribed, total={len(self._clients)}")
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a client queue."""
        async with self._lock:
            if q in self._clients:
                self._clients.remove(q)
        log.debug(f"SSE client unsubscribed, total={len(self._clients)}")

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        """Broadcast an event to all connected clients.

        Slow-client policy (coalesce + resync, not disconnect): when a client
        queue is full, drain it and enqueue a single ``sync.resync_required``
        marker. The connection stays open and the frontend does one full
        refetch on receipt, so no event stream is silently lost.
        """
        payload = json.dumps({"event": event, "data": data, "ts": time.time()})
        async with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self._coalesce_slow_client(q)

    def _coalesce_slow_client(self, q: asyncio.Queue) -> None:
        """Drain a full client queue and mark it for resync."""
        drained = 0
        try:
            while True:
                q.get_nowait()
                drained += 1
        except asyncio.QueueEmpty:
            pass
        marker = json.dumps({"event": "sync.resync_required", "data": {}, "ts": time.time()})
        try:
            q.put_nowait(marker)
        except asyncio.QueueFull:
            pass
        log.warning(
            f"SSE slow client coalesced: dropped {drained} events, "
            f"sent sync.resync_required (clients={len(self._clients)})"
        )


# Global singleton
_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


async def publish_event(event: str, data: dict[str, Any]) -> None:
    await _bus.publish(event, data)
