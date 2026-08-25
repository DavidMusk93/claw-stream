"""backend/routers/events.py — Server-Sent Events endpoint

Single SSE stream replaces all frontend polling:
- sync.status — sync start / complete / error
- sync.progress — live per-phase sync progress (prepare/fetch/covers/write)
- sync.resync_required — client event queue overflowed; refetch state once
- torrent.status — torrent state changes
- torrent.progress — throttled (2s) in-memory progress push, replaces status polling
- cache.update — cache items / metrics changes
- star.ready — newly-added star titles ready
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.routers.auth import require_auth
from core.events import get_bus
from core.logger import get_logger

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(require_auth)])
log = get_logger("events-router")


@router.get("")
async def sse_stream() -> StreamingResponse:
    """Establish SSE connection. Stream JSON events forever until client disconnects."""
    bus = get_bus()
    queue = await bus.subscribe()

    async def event_generator():
        try:
            # Tell the browser to wait 3s before reconnecting after a drop.
            yield "retry: 3000\n\n"
            while True:
                # Wait for next event with heartbeat every 30s to keep connection alive
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ":heartbeat\n\n"
                except Exception:
                    log.exception("SSE event delivery error")
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SSE generator error")
        finally:
            try:
                await asyncio.shield(bus.unsubscribe(queue))
            except Exception:
                log.exception("SSE unsubscribe error")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
