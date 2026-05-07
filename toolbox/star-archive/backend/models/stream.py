from __future__ import annotations

from pydantic import BaseModel


class StreamCheckResponse(BaseModel):
    """视频流检查响应"""
    hash: str
    cached: bool = False
    head_ready: bool = False
    path: str | None = None
    size: int = 0
    mime: str = "video/mp4"
