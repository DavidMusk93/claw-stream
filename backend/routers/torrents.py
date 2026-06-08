from __future__ import annotations

import asyncio
import json
import os
import re
import time

import duckdb
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Any

from backend.models import TorrentStatus, TorrentAddRequest, TorrentAddResponse, SeekRequest, ProgressRequest
from core import get_logger

router = APIRouter(prefix="/torrent", tags=["torrents"])
log = get_logger("torrents-router")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


def _is_primary_title(work_code: str) -> bool:
    """Query DB to check if this title is the actor's first (latest) title."""
    if not work_code:
        return False
    try:
        conn = duckdb.connect(DB_PATH)
        try:
            row = conn.execute("""
                SELECT 1 FROM stars s
                JOIN (
                    SELECT star_id, code,
                        ROW_NUMBER() OVER (
                            PARTITION BY star_id
                            ORDER BY release_date_sort DESC NULLS LAST
                        ) AS rn
                    FROM titles
                ) t ON t.star_id = s.id AND t.rn = 1
                WHERE t.code = ?
            """, [work_code.upper()]).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def _resolve_magnet(magnet: str) -> str:
    """如果 magnet 只有 bare hash，从数据库查找包含 tracker 的完整 magnet。"""
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    if not m:
        return magnet
    hash_str = m.group(1).lower()
    # 已经有 tracker 就原样返回
    if "tr=" in magnet:
        return magnet
    try:
        conn = duckdb.connect(DB_PATH)
        try:
            # 大宽表：从 titles.all_magnets JSON 中查找匹配的 hash
            row = conn.execute("""
                SELECT json_extract_string(
                    list_filter(
                        cast(all_magnets as JSON[]),
                        x -> json_extract_string(x, '$.hash') = ?
                    )[1],
                    '$.magnet'
                ) as magnet
                FROM titles
                WHERE magnet_hash = ? OR json_contains(all_magnets, json_object('hash', ?))
                LIMIT 1
            """, [hash_str, hash_str, hash_str]).fetchone()
            if row and row[0] and "tr=" in row[0]:
                full = row[0].replace("&amp;", "&")
                return full
        finally:
            conn.close()
    except Exception:
        pass
    return magnet


def get_engine(request: Request) -> Any:
    return request.app.state.engine


@router.get("/status/{hash_str}", response_model=TorrentStatus)
async def get_torrent_status(hash_str: str, engine: Any = Depends(get_engine)):
    """Get torrent download status."""
    status = await asyncio.to_thread(engine.get_status, hash_str)
    if not status:
        raise HTTPException(status_code=404, detail="Not found")
    return TorrentStatus(**status)


@router.post("/add", response_model=TorrentAddResponse)
async def add_torrent(req: TorrentAddRequest, engine: Any = Depends(get_engine)):
    """Add a torrent by magnet link."""
    if not req.magnet:
        raise HTTPException(status_code=400, detail="Missing magnet")

    magnet = _resolve_magnet(req.magnet)
    # add_torrent does synchronous I/O (save metadata, scan filesystem).
    # Run in thread pool so the event loop stays responsive.
    info = await asyncio.to_thread(engine.add_torrent, magnet, prefetch=req.prefetch)
    if not info:
        raise HTTPException(status_code=400, detail="Invalid magnet")

    hash_str = info["hash"]

    # Check if this is the actor's first title to decide cache retention
    work_code = info.get("work_code")
    if work_code:
        is_primary = await asyncio.to_thread(_is_primary_title, work_code)
        if is_primary:
            await asyncio.to_thread(engine.set_keep_cache, hash_str, True)
            log.info(f"add_torrent: {hash_str[:12]}... primary title {work_code}, cache will be kept")
        else:
            await asyncio.to_thread(engine.set_keep_cache, hash_str, False)
            log.info(f"add_torrent: {hash_str[:12]}... non-primary {work_code}, cache will be removed on pause")

    # Do NOT wait for has_metadata here — that blocks the event loop and
    # serializes playback startup. Frontend already polls /api/check.
    status = await asyncio.to_thread(engine.get_status, hash_str)
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
    ok = await asyncio.to_thread(engine.apply_seek_priority, req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}


@router.post("/progress")
async def progress_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """定期报告播放进度，引擎滑动下载窗口（±30 piece），其余停止下载。"""
    ok = await asyncio.to_thread(engine.update_play_progress, req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}


@router.post("/pause")
async def pause_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """暂停下载：将所有 piece 优先级设为 0，保留已完成的 piece。"""
    ok = await asyncio.to_thread(engine.pause_download, req.hash)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found")
    return {"ok": True}


@router.post("/resume")
async def resume_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """恢复下载：重新设置 head+tail+当前播放窗口。"""
    ok = await asyncio.to_thread(engine.resume_download, req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}
