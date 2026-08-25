"""backend/routers/sync.py — Actor title sync router

Run scrapers.v2.tasks.sync_titles directly in the main event loop,
coordinated with the global DuckDB serial write queue, completely eliminating cross-process / cross-thread lock conflicts.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.routers.auth import require_auth
from backend.routers.stars import invalidate_stars_cache, CONFIG_PATH
from core import get_logger
from core.events import publish_event

router = APIRouter(prefix="/api/stars", tags=["sync"], dependencies=[Depends(require_auth)])
log = get_logger("sync")

_sync_lock = asyncio.Lock()
_sync_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "log_lines": [],
    "last_error": None,
    "failed": [],
}
_sync_task: asyncio.Task[Any] | None = None


async def _run_sync_bg() -> None:
    """Run sync-titles in the main event loop (fully async, won't block the loop)."""
    global _sync_state
    async with _sync_lock:
        _sync_state["log_lines"] = []
        _sync_state["last_error"] = None
        _sync_state["failed"] = []
        started_at = _sync_state["started_at"]

    try:
        from scrapers.v2.tasks.sync_titles import run as run_sync_titles

        async def _on_progress(progress: dict[str, Any]) -> None:
            await publish_event("sync.progress", progress)

        outcome = await run_sync_titles(CONFIG_PATH, on_progress=_on_progress)
        results = outcome["results"]
        failed = outcome["failed"]
        log_lines = [f"{r.get('name', '?')}: {r.get('count', 0)} titles" for r in results]
        async with _sync_lock:
            _sync_state["log_lines"] = log_lines[-30:]
            _sync_state["failed"] = failed
        log.info("sync completed", extra={"lines": len(log_lines), "failed": len(failed)})
        invalidate_stars_cache()

        # Broadcast completion via SSE
        total_new = sum(r.get("count", 0) for r in results)
        await publish_event("sync.completed", {
            "log_lines": log_lines[-10:],
            "total_new": total_new,
            "failed": [f["name"] for f in failed],
            "elapsed": round(time.time() - started_at, 1) if started_at else 0,
        })
    except Exception as e:
        async with _sync_lock:
            _sync_state["last_error"] = str(e)
        log.error(f"sync exception: {e}", exc_info=True)
        await publish_event("sync.error", {
            "error": str(e)[:200],
            "elapsed": round(time.time() - started_at, 1) if started_at else 0,
        })
    finally:
        async with _sync_lock:
            _sync_state["running"] = False


@router.post("/sync")
async def start_sync() -> JSONResponse:
    """Start actor title sync (background, no duplicate runs)."""
    global _sync_task
    async with _sync_lock:
        if _sync_state["running"] or (_sync_task is not None and not _sync_task.done()):
            return JSONResponse(
                status_code=200,
                content={
                    "status": "running",
                    "started_at": _sync_state["started_at"],
                    "elapsed": round(time.time() - _sync_state["started_at"], 1),
                },
            )
        _sync_state["running"] = True
        _sync_state["started_at"] = time.time()
        _sync_task = asyncio.create_task(_run_sync_bg())

    try:
        await publish_event("sync.started", {"started_at": _sync_state["started_at"]})
    except Exception:
        log.exception("Failed to publish sync.started event")
    return JSONResponse(status_code=202, content={"status": "started"})


@router.get("/sync")
async def get_sync_status() -> dict[str, Any]:
    """Query sync status."""
    async with _sync_lock:
        elapsed = None
        if _sync_state["started_at"]:
            elapsed = round(time.time() - _sync_state["started_at"], 1)
        return {
            "running": _sync_state["running"],
            "started_at": _sync_state["started_at"],
            "elapsed": elapsed,
            "last_error": _sync_state["last_error"],
            "failed": _sync_state.get("failed", []),
            "log_lines": _sync_state["log_lines"][-10:],
        }
