"""scrapers/v2/schemas.py — 爬虫数据结构定义

所有抽取结果统一用 Pydantic 校验，保证类型安全。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MagnetCandidate(BaseModel):
    """单个磁力链接候选"""

    magnet: str
    resolution: str = ""
    size: str = ""           # 原始大小字符串，如 "5.2GB"
    seed: int = 0
    leech: int = 0


class VideoItem(BaseModel):
    """ijavtorrent 作品卡片"""

    code: str = Field(..., pattern=r"^[A-Z0-9-]+$")
    title: str = ""
    release_date: str | None = None   # MM/DD/YYYY
    views: int | None = None
    likes: int | None = None
    cover_url: str | None = None
    star_count: int = 0
    magnets: list[MagnetCandidate] = []
    all_magnet_urls: list[str] = []

    @field_validator("code")
    @classmethod
    def _upper_code(cls, v: str) -> str:
        return v.upper()


class BestMagnet(BaseModel):
    """评分后的最佳磁力链接"""

    magnet: str = ""
    resolution: str = ""
    size: str = ""
    all_magnet_urls: list[str] = []


class StarConfig(BaseModel):
    """config.json 中的单个 star 配置"""

    name: str
    code: str
    handle: str = ""
    star_page_url: str = ""
    type: str = "solo"
    note: str = ""
