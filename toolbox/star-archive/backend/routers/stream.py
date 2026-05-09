from __future__ import annotations

import asyncio
import os
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from typing import Any

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


@stream_router.get("/{hash_str}")
async def stream_video(hash_str: str, request: Request, engine: Any = Depends(get_engine)):
    """Serve video stream with Range support.

    大文件流式播放使用线程池执行文件 I/O，避免阻塞 FastAPI 事件循环。
    不带 Range 头的请求返回前 1MB（200），避免 Safari 收到 416 后报 code=4。
    带 Range 头的请求返回 206 Partial Content；若请求范围全是 hole 则返回 416。
    """
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if not path:
        raise HTTPException(status_code=404, detail="Video not found")

    total_size = os.path.getsize(path)
    range_hdr = request.headers.get("Range")

    start, end = 0, min(total_size - 1, 1024 * 1024 - 1)
    if range_hdr:
        try:
            start, end = _parse_range(range_hdr, total_size)
        except ValueError:
            headers = {"Accept-Ranges": "bytes", "Content-Type": mime}
            raise HTTPException(status_code=416, headers=headers, detail="Invalid range")

    # 将同步文件 I/O 放到线程池，避免阻塞事件循环
    data = await asyncio.to_thread(read_video_range, hash_str, start, end, engine)
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
async def check_stream(hash_str: str):
    """Check if video head is ready for playback."""
    local_path, local_size, head_ready, mime = find_video_state(hash_str)
    return StreamCheckResponse(
        hash=hash_str,
        cached=local_size > 1024 * 1024,
        head_ready=head_ready,
        path=local_path or "",
        size=local_size,
        mime=mime,
    )
