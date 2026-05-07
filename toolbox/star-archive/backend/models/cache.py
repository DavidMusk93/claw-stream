from __future__ import annotations

from pydantic import BaseModel


class CacheStatus(BaseModel):
    """单个缓存项状态"""
    hash: str
    name: str | None = None
    ready: bool = False
    cached: bool = False
    head_ready: bool = False
    peers: int = 0
    progress: float = 0.0
    download_rate: float = 0.0
    upload_rate: float = 0.0
    video_file: str | None = None
    video_size: int = 0
    local_size: int = 0
    mime: str = "video/mp4"
    state: str = ""


class CacheMetrics(BaseModel):
    """缓存总体指标"""
    total: int = 0
    completed: int = 0
    downloading: int = 0
    used_bytes: int = 0
    used_human: str = "0 B"
    max_bytes: int = 0
    max_human: str = "0 B"
