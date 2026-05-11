from __future__ import annotations

import asyncio
import os
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from typing import Any

import libtorrent as lt

from backend.services.torrent_engine import find_video_state
from backend.services.video_stream import read_video_range
from backend.models import StreamCheckResponse
from core import get_logger

stream_router = APIRouter(prefix="/stream", tags=["stream"])
check_router = APIRouter(prefix="/api/check", tags=["stream"])
log = get_logger("stream-router")


def get_engine(request: Request) -> Any:
    return request.app.state.engine


def _parse_range(range_hdr: str, total_size: int) -> tuple[int, int]:
    """解析 Range 请求头，返回 (start, end)。格式无效时抛出 ValueError。"""
    if not range_hdr.startswith("bytes="):
        raise ValueError("Invalid range unit")
    parts = range_hdr.replace("bytes=", "").split("-")
    if len(parts) != 2:
        raise ValueError("Invalid range format")

    start = int(parts[0]) if parts[0] else 0
    end = int(parts[1]) if parts[1] else total_size - 1

    if start < 0 or end >= total_size or start > end:
        raise ValueError("Invalid range values")

    return start, end


def _is_torrent_checking(engine: Any, hash_str: str) -> bool:
    """检查指定 torrent 是否处于 checking_files 状态。"""
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if not info:
        return False
    try:
        return info["handle"].status().state == lt.torrent_status.checking_files
    except Exception:
        return False


@stream_router.get("/{hash_str}")
async def stream_video(hash_str: str, request: Request, engine: Any = Depends(get_engine)):
    """Serve video stream with Range support.

    大文件流式播放使用线程池执行文件 I/O，避免阻塞 FastAPI 事件循环。
    不带 Range 头的请求返回前 8MB（200），避免 Safari 收到 416 后报 code=4。
    带 Range 头的请求返回 206 Partial Content；若请求范围全是 hole 则返回 416。
    若 torrent 处于 checking_files 状态返回 503，防止读取到不一致数据。
    """
    import time as _time
    t0 = _time.perf_counter()
    path, real_size, head_ready, mime = await asyncio.to_thread(find_video_state, hash_str)
    t1 = _time.perf_counter()
    if not path:
        raise HTTPException(status_code=404, detail="Video not found")

    # GC protection: any stream request counts as active use
    await asyncio.to_thread(engine.touch, hash_str)

    # Tiered cache: mark this torrent as actively played
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        info["_last_play_time"] = _time.time()
        info["_play_count"] = info.get("_play_count", 0) + 1

    # Recheck only reads disk data to compute hashes; it does not modify
    # the file. Streaming from already-verified head/tail regions during
    # recheck is safe — hole detection below catches any missing data.
    t2 = _time.perf_counter()
    t3 = _time.perf_counter()

    total_size = os.path.getsize(path)
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

    # 文件 I/O 通过 read_video_range 内部异步化，不再阻塞事件循环
    t4 = _time.perf_counter()
    data = await read_video_range(hash_str, start, end, engine)
    t5 = _time.perf_counter()
    actual_size = len(data)

    is_hole = actual_size > 0 and not any(data)
    log.debug(
        "stream_video response",
        extra={
            "hash": hash_str[:12],
            "range": f"{start}-{end}",
            "requested_size": end - start + 1,
            "actual_size": actual_size,
            "hole": is_hole,
            "mime": mime,
            "timing_ms": {
                "find_state": round((t1-t0)*1000, 2),
                "check_checking": round((t3-t2)*1000, 2),
                "read_range": round((t5-t4)*1000, 2),
                "total": round((_time.perf_counter()-t0)*1000, 2),
            },
        },
    )

    if range_hdr:
        if actual_size == 0:
            # 请求范围全是 hole，返回 416 + 空范围 Content-Range
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
        # Safari 等浏览器首次请求可能不带 Range；返回 200 + 前 1MB 避免 416 触发 code=4
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(actual_size),
            "Content-Type": mime,
        }
        return Response(content=data, status_code=200, headers=headers)


@check_router.get("/{hash_str}", response_model=StreamCheckResponse)
async def check_stream(hash_str: str, engine: Any = Depends(get_engine)):
    """Check if video head is ready for playback."""
    local_path, local_size, head_ready_fs, mime = await asyncio.to_thread(find_video_state, hash_str)

    # GC protection: any check request counts as active use
    await asyncio.to_thread(engine.touch, hash_str)

    # Allow playback if filesystem head is ready, even during recheck.
    # Recheck only re-validates hashes; already-downloaded head data is safe.
    head_ready = head_ready_fs

    return StreamCheckResponse(
        hash=hash_str,
        cached=local_size > 1024 * 1024,
        head_ready=head_ready,
        path=local_path or "",
        size=local_size,
        mime=mime,
    )
