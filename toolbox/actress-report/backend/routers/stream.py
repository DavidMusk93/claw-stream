from __future__ import annotations

import os
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from typing import Any

from backend.services.torrent_engine import find_video_state
from backend.services.video_stream import read_video_range, read_video_full
from backend.models import StreamCheckResponse

stream_router = APIRouter(prefix="/stream", tags=["stream"])
check_router = APIRouter(prefix="/api/check", tags=["stream"])


def get_engine(request: Request) -> Any:
    return request.app.state.engine


@stream_router.get("/{hash_str}")
async def stream_video(hash_str: str, request: Request, engine: Any = Depends(get_engine)):
    """Serve video stream with Range support."""
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if not path:
        raise HTTPException(status_code=404, detail="Video not found")

    range_hdr = request.headers.get("Range")
    total_size = os.path.getsize(path)

    if range_hdr:
        parts = range_hdr.replace("bytes=", "").split("-")
        start = int(parts[0])
        end = int(parts[1]) if parts[1] else total_size - 1
        chunk_size = (end - start) + 1

        data = read_video_range(hash_str, start, end, engine)
        actual_size = len(data)

        headers = {
            "Content-Range": f"bytes {start}-{start + actual_size - 1}/{total_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(actual_size),
            "Content-Type": mime,
        }
        return Response(content=data, status_code=206, headers=headers)
    else:
        data = read_video_full(hash_str, engine)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(data)),
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
