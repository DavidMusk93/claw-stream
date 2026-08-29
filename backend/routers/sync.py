"""backend/routers/sync.py — Actor title sync router

Run scrapers.v2.tasks.sync_titles directly in the main event loop,
coordinated with the global DuckDB serial write queue, completely eliminating cross-process / cross-thread lock conflicts.

Sync is both manual (POST /api/stars/sync) and scheduled: a background
asyncio task re-runs it every SYNC_INTERVAL_HOURS. Every run is recorded
in the sync_runs table so the UI can show last-update time and history.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.routers.auth import require_auth
from backend.routers.stars import invalidate_stars_cache, CONFIG_PATH
from core import db, get_logger
from core.db.write_queue import db_write
from core.events import publish_event

router = APIRouter(prefix="/api/stars", tags=["sync"], dependencies=[Depends(require_auth)])
log = get_logger("sync")

SYNC_INTERVAL_HOURS = 6
SYNC_INTERVAL_SEC = SYNC_INTERVAL_HOURS * 3600

_sync_lock = asyncio.Lock()
_sync_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "log_lines": [],
    "last_error": None,
    "failed": [],
}
_sync_task: asyncio.Task[Any] | None = None
_scheduler_task: asyncio.Task[Any] | None = None
_next_scheduled_at: float | None = None


async def _run_sync_bg(trigger: str) -> None:
    """Run sync-titles in the main event loop (fully async, won't block the loop)."""
    global _sync_state
    async with _sync_lock:
        _sync_state["log_lines"] = []
        _sync_state["last_error"] = None
        _sync_state["failed"] = []
        started_at = _sync_state["started_at"]

    run_id: int | None = None
    try:
        run_id = await db_write(db.insert_sync_run, trigger)
    except Exception:
        log.exception("failed to record sync run start")

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

        if run_id is not None:
            try:
                await db_write(
                    db.finish_sync_run, run_id, "success",
                    outcome.get("total_new", 0), outcome.get("total_updated", 0),
                    len(failed), None,
                )
            except Exception:
                log.exception("failed to record sync run finish")

        # Broadcast completion via SSE
        total_new = outcome.get("total_new", 0)
        await publish_event("sync.completed", {
            "trigger": trigger,
            "log_lines": log_lines[-10:],
            "total_new": total_new,
            "failed": [f["name"] for f in failed],
            "elapsed": round(time.time() - started_at, 1) if started_at else 0,
        })
    except Exception as e:
        async with _sync_lock:
            _sync_state["last_error"] = str(e)
        log.error(f"sync exception: {e}", exc_info=True)
        if run_id is not None:
            try:
                await db_write(db.finish_sync_run, run_id, "error", 0, 0, 0, str(e)[:500])
            except Exception:
                log.exception("failed to record sync run error")
        await publish_event("sync.error", {
            "trigger": trigger,
            "error": str(e)[:200],
            "elapsed": round(time.time() - started_at, 1) if started_at else 0,
        })
    finally:
        async with _sync_lock:
            _sync_state["running"] = False


async def trigger_sync(trigger: str = "manual") -> dict[str, Any]:
    """Start a sync if none is running. Returns a status dict."""
    global _sync_task
    async with _sync_lock:
        if _sync_state["running"] or (_sync_task is not None and not _sync_task.done()):
            return {
                "status": "running",
                "started_at": _sync_state["started_at"],
                "elapsed": round(time.time() - _sync_state["started_at"], 1),
            }
        _sync_state["running"] = True
        _sync_state["started_at"] = time.time()
        _sync_task = asyncio.create_task(_run_sync_bg(trigger))

    try:
        await publish_event("sync.started", {"trigger": trigger, "started_at": _sync_state["started_at"]})
    except Exception:
        log.exception("Failed to publish sync.started event")
    return {"status": "started", "trigger": trigger}


# ── Scheduler ───────────────────────────────────────────────────────

async def _scheduler_loop() -> None:
    """Re-run sync every SYNC_INTERVAL_HOURS, accounting for the last run.

    On startup, if the last recorded run is already older than the interval
    (e.g. the service was down), sync soon; otherwise wait out the remainder.
    """
    global _next_scheduled_at
    delay = SYNC_INTERVAL_SEC
    try:
        runs = await db_write(db.list_sync_runs, 1)
        if runs and runs[0]["finished_at"]:
            from datetime import datetime
            finished = datetime.strptime(runs[0]["finished_at"], "%Y-%m-%d %H:%M:%S").timestamp()
            delay = max(60.0, SYNC_INTERVAL_SEC - (time.time() - finished))
    except Exception:
        log.exception("scheduler: failed to read last sync run")

    while True:
        _next_scheduled_at = time.time() + delay
        log.info(f"sync scheduler: next run in {delay / 3600:.2f}h")
        await asyncio.sleep(delay)
        delay = SYNC_INTERVAL_SEC
        try:
            await trigger_sync("scheduled")
        except Exception:
            log.exception("scheduler: scheduled sync failed to start")


def start_sync_scheduler() -> None:
    """Idempotently start the background sync scheduler (called from lifespan)."""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    log.info(f"sync scheduler started (every {SYNC_INTERVAL_HOURS}h)")


# ── Routes ──────────────────────────────────────────────────────────

@router.post("/sync")
async def start_sync() -> JSONResponse:
    """Start actor title sync (background, no duplicate runs)."""
    result = await trigger_sync("manual")
    status = 200 if result["status"] == "running" else 202
    return JSONResponse(status_code=status, content=result)


@router.get("/sync")
async def get_sync_status() -> dict[str, Any]:
    """Query sync status, including last run and next scheduled run."""
    async with _sync_lock:
        elapsed = None
        if _sync_state["started_at"]:
            elapsed = round(time.time() - _sync_state["started_at"], 1)
        state = {
            "running": _sync_state["running"],
            "started_at": _sync_state["started_at"],
            "elapsed": elapsed,
            "last_error": _sync_state["last_error"],
            "failed": _sync_state.get("failed", []),
            "log_lines": _sync_state["log_lines"][-10:],
        }

    try:
        runs = await db_write(db.list_sync_runs, 5)
    except Exception:
        runs = []
    state["recent_runs"] = runs
    state["last_run"] = runs[0] if runs else None
    state["next_scheduled_at"] = _next_scheduled_at
    state["sync_interval_hours"] = SYNC_INTERVAL_HOURS
    return state
