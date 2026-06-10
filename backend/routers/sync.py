"""backend/routers/sync.py — Actor title sync router

Run scrapers.v2.tasks.sync_titles directly in the main event loop,
coordinated with the global DuckDB serial write queue, completely eliminating cross-process / cross-thread lock conflicts.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi import APIRouter

from backend.routers.stars import invalidate_stars_cache, CONFIG_PATH
from core import get_logger
from core.events import publish_event

router = APIRouter(prefix="/api/stars", tags=["sync"])
log = get_logger("sync")

_sync_lock = threading.Lock()
_sync_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "log_lines": [],
    "last_error": None,
}


async def _run_sync_bg() -> None:
    """Run sync-titles in the main event loop (fully async, won't block the loop)."""
    global _sync_state
    _sync_state["log_lines"] = []
    _sync_state["last_error"] = None
    started_at = _sync_state["started_at"]

    try:
        from scrapers.v2.tasks.sync_titles import run as run_sync_titles

        results = await run_sync_titles(CONFIG_PATH)
        log_lines = [f"{r['name']}: {r['count']} titles" for r in results]
        _sync_state["log_lines"] = log_lines[-30:]
        log.info("sync completed", extra={"lines": len(log_lines)})
        invalidate_stars_cache()

        # Broadcast completion via SSE
        total_new = sum(r.get("count", 0) for r in results)
        await publish_event("sync.completed", {
            "log_lines": _sync_state["log_lines"][-10:],
            "total_new": total_new,
            "elapsed": round(time.time() - started_at, 1) if started_at else 0,
        })
    except Exception as e:
        _sync_state["last_error"] = str(e)
        log.error(f"sync exception: {e}", exc_info=True)
        await publish_event("sync.error", {
            "error": str(e)[:200],
            "elapsed": round(time.time() - started_at, 1) if started_at else 0,
        })
    finally:
        _sync_state["running"] = False


@router.post("/sync")
async def start_sync() -> dict[str, Any]:
    """Start actor title sync (background, no duplicate runs)."""
    with _sync_lock:
        if _sync_state["running"]:
            return {
                "status": "running",
                "started_at": _sync_state["started_at"],
                "elapsed": round(time.time() - _sync_state["started_at"], 1),
            }
        _sync_state["running"] = True
        _sync_state["started_at"] = time.time()
        asyncio.create_task(_run_sync_bg())
        await publish_event("sync.started", {"started_at": _sync_state["started_at"]})
        return {"status": "started"}


@router.get("/sync")
async def get_sync_status() -> dict[str, Any]:
    """Query sync status."""
    elapsed = None
    if _sync_state["started_at"]:
        elapsed = round(time.time() - _sync_state["started_at"], 1)
    return {
        "running": _sync_state["running"],
        "started_at": _sync_state["started_at"],
        "elapsed": elapsed,
        "last_error": _sync_state["last_error"],
        "log_lines": _sync_state["log_lines"][-10:],
    }
