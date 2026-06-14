from __future__ import annotations

import asyncio
import os
from fastapi import APIRouter, Request, Depends, HTTPException, Path
from fastapi.responses import Response, JSONResponse
from typing import Any

import libtorrent as lt

from backend.services.torrent_engine import find_video_state
from backend.services.video_stream import read_video_range
from backend.models import StreamCheckResponse
from backend.routers.auth import require_auth
from core import get_logger

stream_router = APIRouter(prefix="/stream", tags=["stream"], dependencies=[Depends(require_auth)])
check_router = APIRouter(prefix="/api/check", tags=["stream"], dependencies=[Depends(require_auth)])
log = get_logger("stream-router")

HASH_PATTERN = r"^[a-fA-F0-9]{40}$"


def get_engine(request: Request) -> Any:
    return request.app.state.engine


def _parse_range(range_hdr: str, total_size: int) -> tuple[int, int]:
    """Parse Range request header, return (start, end). Raises ValueError on invalid format."""
    if not range_hdr.startswith("bytes="):
        raise ValueError("Invalid range unit")
    parts = range_hdr.replace("bytes=", "").split("-")
    if len(parts) != 2:
        raise ValueError("Invalid range format")

    if parts[0] and parts[1]:
        # Standard range: bytes=start-end
        start = int(parts[0])
        end = int(parts[1])
    elif parts[0] and not parts[1]:
        # Prefix range: bytes=start-
        start = int(parts[0])
        end = total_size - 1
    elif not parts[0] and parts[1]:
        # Suffix range: bytes=-suffix_len
        suffix_len = int(parts[1])
        if suffix_len <= 0 or suffix_len > total_size:
            raise ValueError("Invalid suffix range")
        start = total_size - suffix_len
        end = total_size - 1
    else:
        raise ValueError("Invalid range format")

    if start < 0 or end >= total_size or start > end:
        raise ValueError("Invalid range values")

    return start, end


def _is_torrent_checking(engine: Any, hash_str: str) -> bool:
    """Check whether the specified torrent is in checking_files state."""
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if not info:
        return False
    try:
        return info["handle"].status().state == lt.torrent_status.checking_files
    except Exception:
        return False


@stream_router.get("/{hash_str}")
async def stream_video(
    request: Request,
    hash_str: str = Path(..., pattern=HASH_PATTERN),
    engine: Any = Depends(get_engine),
):
    """Serve video stream with Range support.

    Large-file streaming playback uses the thread pool for file I/O, avoiding blocking the FastAPI event loop.
    Requests without a Range header return the first 8MB (200), preventing Safari from throwing code=4 after receiving 416.
    Requests with a Range header return 206 Partial Content; if the requested range is entirely a hole, return 416.
    If the torrent is in checking_files state, return 503 to prevent reading inconsistent data.
    """
    import time as _time
    t0 = _time.perf_counter()

    if _is_torrent_checking(engine, hash_str):
        raise HTTPException(
            status_code=503,
            detail="Torrent is verifying files — retry shortly",
        )

    # Prefer the target file already selected by _pick_video_file, to avoid accidentally selecting ad files during download
    preferred_path = None
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        preferred_path = info.get("video_path")
    path, real_size, head_ready, mime = await asyncio.to_thread(find_video_state, hash_str, preferred_path)
    t1 = _time.perf_counter()
    if not path:
        # File doesn't exist yet, but torrent is in engine.
        # Trigger _set_stream_window to fix finished-state deadlock
        # (truncate + force_recheck), then return 503 for frontend to retry.
        with engine.lock:
            info = engine.torrents.get(hash_str)
        if info:
            handle = info.get("handle")
            if handle:
                await asyncio.to_thread(
                    engine._set_stream_window, handle, info, 0.0, 0.0, 30
                )
        raise HTTPException(
            status_code=503,
            detail="Video not ready, download triggered — retry shortly",
        )

    # GC protection: any stream request counts as active use
    await asyncio.to_thread(engine.touch, hash_str)

    # Tiered cache: mark this torrent as actively played
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        info["_last_play_time"] = _time.time()
        info["_play_count"] = info.get("_play_count", 0) + 1

    # File exists but head not ready (finished-state deadlock).
    # Trigger _set_stream_window to fix via truncate+force_recheck.
    if path and not head_ready:
        with engine.lock:
            info = engine.torrents.get(hash_str)
        if info:
            handle = info.get("handle")
            if handle:
                await asyncio.to_thread(
                    engine._set_stream_window, handle, info, 0.0, 0.0, 30
                )

    total_size = await asyncio.to_thread(os.path.getsize, path)
    range_hdr = request.headers.get("Range")

    # Default chunk for non-Range requests matches MAX_CHUNK in read_video_range
    DEFAULT_CHUNK = 8 * 1024 * 1024
    start, end = 0, min(total_size - 1, DEFAULT_CHUNK - 1)
    if range_hdr:
        try:
            start, end = _parse_range(range_hdr, total_size)
        except ValueError:
            headers = {
                "Content-Range": f"bytes */{total_size}",
                "Accept-Ranges": "bytes",
                "Content-Type": mime,
            }
            raise HTTPException(status_code=416, headers=headers, detail="Invalid range")

    # File I/O is internally async via read_video_range, no longer blocking the event loop
    t4 = _time.perf_counter()
    data = await read_video_range(hash_str, start, end, engine)
    t5 = _time.perf_counter()
    actual_size = len(data)

    log.debug(
        "stream_video response",
        extra={
            "hash": hash_str[:12],
            "range": f"{start}-{end}",
            "requested_size": end - start + 1,
            "actual_size": actual_size,
            "mime": mime,
            "timing_ms": {
                "find_state": round((t1-t0)*1000, 2),
                "read_range": round((t5-t4)*1000, 2),
                "total": round((_time.perf_counter()-t0)*1000, 2),
            },
        },
    )

    if range_hdr:
        if actual_size == 0:
            # Requested range is entirely a hole, return 416 + empty-range Content-Range
            headers = {
                "Content-Range": f"bytes */{total_size}",
                "Accept-Ranges": "bytes",
                "Content-Type": mime,
            }
            return Response(content=b"", status_code=416, headers=headers)
        headers = {
            "Content-Range": f"bytes {start}-{start + actual_size - 1}/{total_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(actual_size),
            "Content-Type": mime,
        }
        return Response(content=data, status_code=206, headers=headers)
    else:
        # Safari and other browsers may not send Range on the first request; return 200 + first 8MB to avoid 416 triggering code=4
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(actual_size),
            "Content-Type": mime,
        }
        return Response(content=data, status_code=200, headers=headers)


@check_router.get("/{hash_str}", response_model=StreamCheckResponse)
async def check_stream(
    hash_str: str = Path(..., pattern=HASH_PATTERN),
    engine: Any = Depends(get_engine),
):
    """Check if video head is ready for playback."""
    local_path, local_size, head_ready_fs, mime = await asyncio.to_thread(find_video_state, hash_str)

    # GC protection: any check request counts as active use
    await asyncio.to_thread(engine.touch, hash_str)

    # Allow playback if filesystem head is ready, even during recheck.
    # Recheck only re-validates hashes; already-downloaded head data is safe.
    # find_video_state already verifies data presence via SEEK_HOLE/SEEK_DATA.
    response = StreamCheckResponse(
        hash=hash_str,
        cached=local_size > 1024 * 1024,
        head_ready=head_ready_fs,
        path=local_path or "",
        size=local_size,
        mime=mime,
    )

    return JSONResponse(
        content=response.model_dump(),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
