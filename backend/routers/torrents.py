from __future__ import annotations

import asyncio
import json
import os
import re
import time

import duckdb
from fastapi import APIRouter, Request, Depends, HTTPException, Path
from typing import Any

from backend.models import TorrentStatus, TorrentAddRequest, TorrentAddResponse, SeekRequest, ProgressRequest
from backend.routers.auth import require_auth
from core import get_logger
from core.db.connection import _conn as _db_conn

router = APIRouter(prefix="/torrent", tags=["torrents"], dependencies=[Depends(require_auth)])
log = get_logger("torrents-router")

HASH_PATTERN = r"^[a-fA-F0-9]{40}$"

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


def _is_primary_title(work_code: str) -> bool:
    """Query DB to check if this title is the actor's first (latest) title."""
    if not work_code:
        return False
    try:
        conn = _db_conn()
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
    """If the magnet only contains a bare hash, look up the full magnet with trackers from the database."""
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    if not m:
        return magnet
    hash_str = m.group(1).lower()
    # If it already has trackers, return as-is
    if "tr=" in magnet:
        return magnet
    try:
        conn = _db_conn()
        try:
            # Wide table: look up matching hash from titles.all_magnets JSON
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
async def get_torrent_status(
    hash_str: str = Path(..., pattern=HASH_PATTERN),
    engine: Any = Depends(get_engine),
):
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
    try:
        info = await asyncio.to_thread(engine.add_torrent, magnet, prefetch=req.prefetch)
    except Exception as exc:
        log.warning(f"add_torrent failed: {exc}")
        raise HTTPException(status_code=400, detail="Invalid magnet or unable to add torrent")
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
    if not re.fullmatch(HASH_PATTERN, req.hash, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid hash")
    ok = await asyncio.to_thread(engine.apply_seek_priority, req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}


@router.post("/progress")
async def progress_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """Periodically report playback progress; engine slides the download window (±30 pieces), stops downloading the rest."""
    if not re.fullmatch(HASH_PATTERN, req.hash, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid hash")
    ok = await asyncio.to_thread(engine.update_play_progress, req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}


@router.post("/pause")
async def pause_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """Pause download: set all piece priorities to 0, keep completed pieces."""
    if not re.fullmatch(HASH_PATTERN, req.hash, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid hash")
    ok = await asyncio.to_thread(engine.pause_download, req.hash)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found")
    return {"ok": True}


@router.post("/resume")
async def resume_torrent(req: ProgressRequest, engine: Any = Depends(get_engine)):
    """Resume download: re-set head+tail+current playback window."""
    if not re.fullmatch(HASH_PATTERN, req.hash, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid hash")
    ok = await asyncio.to_thread(engine.resume_download, req.hash, req.time, req.duration)
    if not ok:
        raise HTTPException(status_code=404, detail="Torrent not found or not ready")
    return {"ok": True}
