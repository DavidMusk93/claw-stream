"""backend/routers/events.py — Server-Sent Events endpoint

Single SSE stream replaces all frontend polling:
- sync.status — sync start / complete / error
- torrent.status — torrent state changes
- cache.update — cache items / metrics changes
- star.ready — newly-added star titles ready
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.events import get_bus
from core.logger import get_logger

router = APIRouter(prefix="/api/events", tags=["events"])
log = get_logger("events-router")


@router.get("")
async def sse_stream() -> StreamingResponse:
    """Establish SSE connection. Stream JSON events forever until client disconnects."""
    bus = get_bus()
    queue = await bus.subscribe()

    async def event_generator():
        try:
            while True:
                # Wait for next event with heartbeat every 30s to keep connection alive
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ":heartbeat\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            await bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
