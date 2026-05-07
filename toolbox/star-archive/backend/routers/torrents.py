from __future__ import annotations

import time
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Any

from backend.models import TorrentStatus, TorrentAddRequest, TorrentAddResponse, SeekRequest, ProgressRequest

router = APIRouter(prefix="/torrent", tags=["torrents"])


def get_engine(request: Request) -> Any:
    return request.app.state.engine


@router.get("/status/{hash_str}", response_model=TorrentStatus)
async def get_torrent_status(hash_str: str, engine: Any = Depends(get_engine)):
    """Get torrent download status."""
    status = engine.get_status(hash_str)
    if not status:
        raise HTTPException(status_code=404, detail="Not found")
    return TorrentStatus(**status)


@router.post("/add", response_model=TorrentAddResponse)
async def add_torrent(req: TorrentAddRequest, engine: Any = Depends(get_engine)):
    """Add a torrent by magnet link."""
    if not req.magnet:
        raise HTTPException(status_code=400, detail="Missing magnet")

    info = engine.add_torrent(req.magnet, prefetch=req.prefetch)
    if not info:
        raise HTTPException(status_code=400, detail="Invalid magnet")

    hash_str = info["hash"]

    if not req.prefetch:
        engine.set_full_priority(hash_str)

    h = info["handle"]
    for _ in range(20):
        if h.status().has_metadata:
            break
        time.sleep(0.5)

    status = engine.get_status(hash_str)
    return TorrentAddResponse(
        hash=hash_str,
        status="added",
        ready=status["ready"] if status else False,
        peers=status["peers"] if status else 0,
        progress=status["progress"] if status else 0.0,
    )


@router.post("/seek")
async def seek_torrent(req: SeekRequest, engine: Any = Depends(get_engine)):
    """Report current playback position so engine can prioritize pieces ahead of playhead."""
    ok = engine.apply_seek_priority(req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}


@router.post("/progress")
async def progress_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """定期报告播放进度，引擎滑动下载窗口（±30 piece），其余停止下载。"""
    ok = engine.update_play_progress(req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}
