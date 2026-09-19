"""backend/routers/magnets.py — Magnet liveness check router

Validates that a title's primary magnet actually has a live swarm (metadata
fetchable via DHT/trackers). Dead primaries are automatically swapped to the
next live candidate from all_magnets (already sorted best-first by the sync
sink); titles with no live candidate are marked magnet_status='dead' so the
frontend can grey them out.

Triggered manually (POST /api/magnets/check), by scripts/check_magnets.py for
the full historical sweep, and automatically after every successful title
sync (scope=changed). Progress is pushed over SSE.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.routers.auth import require_auth
from backend.services.magnet_checker import MagnetChecker, extract_hash, has_cached_torrent
from core import db, get_logger
from core.db.write_queue import db_write
from core.events import publish_event

router = APIRouter(prefix="/api/magnets", tags=["magnets"], dependencies=[Depends(require_auth)])
log = get_logger("magnet-check")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
CHECK_WORK_DIR = os.path.join(SCRIPT_DIR, "cache", "magnet-check")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images", "titles")

_checker: MagnetChecker | None = None
_check_lock = asyncio.Lock()
_check_state: dict[str, Any] = {
    "running": False,
    "scope": None,
    "total": 0,
    "done": 0,
    "alive": 0,
    "dead": 0,
    "swapped": 0,
    "started_at": None,
}
_check_task: asyncio.Task[Any] | None = None
_last_progress_push = 0.0


def get_checker() -> MagnetChecker:
    """Process-level checker singleton (own libtorrent session, lazy)."""
    global _checker
    if _checker is None:
        _checker = MagnetChecker(CHECK_WORK_DIR)
    return _checker


def shutdown_checker() -> None:
    global _checker
    if _checker is not None:
        _checker.shutdown()
        _checker = None


class CheckRequest(BaseModel):
    scope: str = Field(default="changed", pattern="^(all|dead|unchecked|changed)$")


def _candidates(item: dict[str, Any], skip_hash: str | None) -> list[dict[str, Any]]:
    """Parse all_magnets, dropping the dead primary and malformed rows.

    JSONB columns come back from psycopg3 already parsed; a raw string is
    still accepted for robustness.
    """
    raw = item.get("all_magnets")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    mags = raw if isinstance(raw, list) else []
    return [m for m in mags if isinstance(m, dict) and m.get("magnet") and m.get("hash") != skip_hash]


def _process_title(item: dict[str, Any]) -> dict[str, Any]:
    """Per-title check: primary first, live-candidate fallback when dead.

    Runs on a MagnetChecker worker thread.
    """
    checker = get_checker()
    primary_hash = item.get("magnet_hash") or extract_hash(item["magnet"])
    res = checker.check_one(item["magnet"])
    if res["alive"]:
        return {"alive": True, "status": "ok", "hash": primary_hash, "peers": res["peers"]}

    # Primary is dead — walk the best-first candidate list for a live one.
    for cand in _candidates(item, primary_hash):
        res = checker.check_one(cand["magnet"])
        if res["alive"]:
            log.info(f"swap {item['code']}: dead primary -> {cand['hash'][:12]}...")
            return {
                "alive": True,
                "status": "swapped",
                "hash": cand["hash"],
                "magnet": cand["magnet"],
                "peers": res["peers"],
            }
    return {"alive": False, "status": "dead", "hash": primary_hash}


async def _persist_outcome(item: dict[str, Any], outcome: dict[str, Any]) -> None:
    """Persist one title's check outcome via the serial write queue."""
    from backend.routers.stars import invalidate_stars_cache

    try:
        if outcome["status"] == "ok":
            await db_write(db.update_magnet_check_ok, item["id"], outcome.get("hash"))
        elif outcome["status"] == "swapped":
            await db_write(db.swap_primary_magnet, item["id"], outcome["magnet"], outcome["hash"])
            invalidate_stars_cache()
        else:
            await db_write(db.update_magnet_check_dead, item["id"], outcome.get("hash"))
            invalidate_stars_cache()
    except Exception:
        log.exception(f"failed to persist magnet check for {item.get('code')}")

    global _last_progress_push
    async with _check_lock:
        _check_state["done"] += 1
        if outcome["status"] == "ok":
            _check_state["alive"] += 1
        elif outcome["status"] == "swapped":
            _check_state["swapped"] += 1
            _check_state["alive"] += 1
        else:
            _check_state["dead"] += 1
        done = _check_state["done"]

    now = time.time()
    if done % 20 == 0 or now - _last_progress_push >= 5:
        _last_progress_push = now
        await publish_event("magnets.check_progress", {
            "scope": _check_state["scope"],
            "total": _check_state["total"],
            "done": done,
            "alive": _check_state["alive"],
            "dead": _check_state["dead"],
            "swapped": _check_state["swapped"],
        })


async def _run_check_bg(scope: str) -> None:
    global _check_state, _last_progress_push
    started_at = _check_state["started_at"]
    pending: list[asyncio.Future] = []
    try:
        titles = await db_write(db.load_titles_for_magnet_check, scope)
        # Disk-is-truth shortcut: a cached .torrent proves the swarm works.
        network_items: list[dict[str, Any]] = []
        cached_ok = 0
        for t in titles:
            h = t.get("magnet_hash") or extract_hash(t["magnet"])
            if h and has_cached_torrent(CACHE_DIR, h):
                pending.append(asyncio.ensure_future(
                    _persist_outcome(t, {"alive": True, "status": "ok", "hash": h})
                ))
                cached_ok += 1
            else:
                network_items.append(t)

        async with _check_lock:
            _check_state["total"] = len(titles)
        log.info(f"magnet check [{scope}]: {len(titles)} titles ({cached_ok} cached, {len(network_items)} network)")

        loop = asyncio.get_running_loop()

        def on_result(item: dict[str, Any], outcome: dict[str, Any]) -> None:
            future = asyncio.run_coroutine_threadsafe(_persist_outcome(item, outcome), loop)
            pending.append(asyncio.wrap_future(future, loop=loop))

        if network_items:
            await asyncio.to_thread(
                get_checker().check_batch, network_items, _process_title, on_result
            )

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        async with _check_lock:
            summary = {k: _check_state[k] for k in ("scope", "total", "done", "alive", "dead", "swapped")}
        summary["elapsed"] = round(time.time() - started_at, 1) if started_at else 0
        log.info(f"magnet check finished: {summary}")
        await publish_event("magnets.check_completed", summary)
    except Exception as e:
        log.error(f"magnet check exception: {e}", exc_info=True)
        await publish_event("magnets.check_error", {"scope": scope, "error": str(e)[:200]})
    finally:
        async with _check_lock:
            _check_state["running"] = False


async def trigger_check(scope: str = "changed") -> dict[str, Any]:
    """Start a magnet check if none is running. Returns a status dict."""
    global _check_task, _last_progress_push
    async with _check_lock:
        if _check_state["running"] or (_check_task is not None and not _check_task.done()):
            return {
                "status": "running",
                "scope": _check_state["scope"],
                "started_at": _check_state["started_at"],
                "elapsed": round(time.time() - _check_state["started_at"], 1) if _check_state["started_at"] else 0,
            }
        _check_state.update({
            "running": True, "scope": scope, "total": 0, "done": 0,
            "alive": 0, "dead": 0, "swapped": 0, "started_at": time.time(),
        })
        _last_progress_push = 0.0
        _check_task = asyncio.create_task(_run_check_bg(scope))

    try:
        await publish_event("magnets.check_started", {"scope": scope, "started_at": _check_state["started_at"]})
    except Exception:
        log.exception("failed to publish magnets.check_started event")
    return {"status": "started", "scope": scope}


@router.post("/check")
async def start_check(req: CheckRequest) -> JSONResponse:
    """Start a magnet liveness check (background, no duplicate runs)."""
    result = await trigger_check(req.scope)
    status = 200 if result["status"] == "running" else 202
    return JSONResponse(status_code=status, content=result)


@router.get("/check")
async def get_check_status() -> dict[str, Any]:
    """Query magnet check progress."""
    async with _check_lock:
        elapsed = None
        if _check_state["started_at"]:
            elapsed = round(time.time() - _check_state["started_at"], 1)
        return {**_check_state, "elapsed": elapsed}


@router.post("/purge-dead")
async def purge_dead(request: Request) -> dict[str, Any]:
    """Delete unplayable titles (dead-checked magnets or no magnet at all).

    Liked titles (user_liked=1) are never deleted. Cached torrents of deleted
    rows become orphans and are garbage-collected right away; cover image
    directories are removed as well.
    """
    from backend.routers.stars import invalidate_stars_cache

    async with _check_lock:
        if _check_state["running"]:
            return JSONResponse(
                status_code=409,
                content={"detail": "magnet check is running; purge after it finishes"},
            )

    unplayable = await db_write(db.list_unplayable_titles)
    liked = [t for t in unplayable if t["user_liked"]]
    deletable = [t for t in unplayable if not t["user_liked"]]

    deleted = await db_write(db.delete_titles_by_ids, [t["id"] for t in deletable])

    def _remove_covers() -> int:
        import shutil
        removed = 0
        for t in deletable:
            cover_dir = os.path.join(IMAGES_DIR, t["code"].lower())
            if os.path.isdir(cover_dir):
                shutil.rmtree(cover_dir, ignore_errors=True)
                removed += 1
        return removed

    covers_removed = await asyncio.to_thread(_remove_covers)

    orphans_removed = 0
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            orphans_removed = await asyncio.to_thread(engine.gc_orphaned_torrents)
        except Exception:
            log.exception("gc_orphaned_torrents failed after purge")

    invalidate_stars_cache()
    summary = {
        "deleted": deleted,
        "liked_kept": len(liked),
        "liked_codes": [t["code"] for t in liked],
        "covers_removed": covers_removed,
        "orphans_removed": orphans_removed,
    }
    log.info(f"purged unplayable titles: {summary}")
    await publish_event("magnets.purged", summary)
    return summary
