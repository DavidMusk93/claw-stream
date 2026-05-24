"""backend/routers/sync.py — 女优作品同步路由

核心变更：取消 subprocess，改为进程内直接调用 scrapers.v2.tasks.sync_titles，
配合全局 DuckDB 串行写队列，彻底消除跨进程锁冲突。
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi import APIRouter

from backend.routers.stars import invalidate_stars_cache
from core import get_logger

router = APIRouter(prefix="/api/stars", tags=["sync"])
log = get_logger("sync")

_sync_lock = threading.Lock()
_sync_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "log_lines": [],
    "last_error": None,
}


def _run_sync_bg() -> None:
    """后台线程：进程内运行 sync-titles，消除跨进程 DuckDB 锁冲突。"""
    global _sync_state
    _sync_state["log_lines"] = []
    _sync_state["last_error"] = None

    try:
        from scrapers.v2.tasks.sync_titles import run as run_sync_titles

        # 在线程中创建独立事件循环运行 async 爬虫
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(run_sync_titles("config.json"))
        log_lines = [f"{r['name']}: {r['count']} titles" for r in results]
        _sync_state["log_lines"] = log_lines[-30:]
        log.info("sync completed", extra={"lines": len(log_lines)})
        invalidate_stars_cache()
    except Exception as e:
        _sync_state["last_error"] = str(e)
        log.error(f"sync exception: {e}", exc_info=True)
    finally:
        _sync_state["running"] = False


@router.post("/sync")
async def start_sync() -> dict[str, Any]:
    """启动女优作品同步（后台运行，不可重复启动）。"""
    with _sync_lock:
        if _sync_state["running"]:
            return {
                "status": "running",
                "started_at": _sync_state["started_at"],
                "elapsed": round(time.time() - _sync_state["started_at"], 1),
            }
        _sync_state["running"] = True
        _sync_state["started_at"] = time.time()
        threading.Thread(target=_run_sync_bg, daemon=True).start()
        return {"status": "started"}


@router.get("/sync")
async def get_sync_status() -> dict[str, Any]:
    """查询同步状态。"""
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
