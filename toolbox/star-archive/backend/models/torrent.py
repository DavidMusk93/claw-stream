from __future__ import annotations

from pydantic import BaseModel


class TorrentStatus(BaseModel):
    """种子状态"""
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
    verified_pieces: int = 0
    quality: str = "SD"


class TorrentAddRequest(BaseModel):
    """添加种子请求"""
    magnet: str
    prefetch: bool = False


class TorrentAddResponse(BaseModel):
    """添加种子响应"""
    hash: str
    status: str
    ready: bool = False
    peers: int = 0
    progress: float = 0.0


class SeekRequest(BaseModel):
    """Seek 进度上报请求"""
    hash: str
    time: float
    duration: float


class ProgressRequest(BaseModel):
    """正常播放中定期进度上报请求"""
    hash: str
    time: float
    duration: float
