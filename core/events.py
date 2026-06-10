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
        """Broadcast an event to all connected clients."""
        payload = json.dumps({"event": event, "data": data, "ts": time.time()})
        dead: list[asyncio.Queue] = []
        async with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            async with self._lock:
                if q in self._clients:
                    self._clients.remove(q)


# Global singleton
_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


async def publish_event(event: str, data: dict[str, Any]) -> None:
    await _bus.publish(event, data)
