from __future__ import annotations

import os
import subprocess
import threading
import time

from fastapi import APIRouter
from typing import Any

from backend.routers.stars import invalidate_stars_cache
from core import get_logger

router = APIRouter(prefix="/api/stars", tags=["sync"])
log = get_logger("sync")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_sync_lock = threading.Lock()
_sync_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "log_lines": [],
    "last_error": None,
    "returncode": None,
}


def _run_sync_bg() -> None:
    """后台线程：运行 search_news.py 抓取女优最新作品。"""
    global _sync_state
    _sync_state["log_lines"] = []
    _sync_state["last_error"] = None
    _sync_state["returncode"] = None

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = SCRIPT_DIR
        proc = subprocess.run(
            [
                os.path.join(SCRIPT_DIR, ".venv", "bin", "python"),
                "scrapers/search_news.py",
                "config.json",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=SCRIPT_DIR,
            env=env,
        )
        _sync_state["log_lines"] = proc.stdout.splitlines()[-30:]
        _sync_state["returncode"] = proc.returncode
        if proc.returncode != 0:
            err = proc.stderr[-2000:] if proc.stderr else "unknown error"
            _sync_state["last_error"] = err
            log.error("sync failed", extra={"returncode": proc.returncode, "stderr": proc.stderr[:500]})
        else:
            log.info("sync completed", extra={"lines": len(_sync_state["log_lines"])})
            invalidate_stars_cache()
    except subprocess.TimeoutExpired:
        _sync_state["last_error"] = "sync timeout after 300s"
        log.error("sync timeout")
    except Exception as e:
        _sync_state["last_error"] = str(e)
        log.error(f"sync exception: {e}")
    finally:
        _sync_state["running"] = False


@router.post("/sync")
async def start_sync() -> dict[str, Any]:
    """启动女优作品同步（后台运行 search_news.py）。不可重复启动。"""
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
