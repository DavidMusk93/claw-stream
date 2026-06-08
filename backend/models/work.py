from __future__ import annotations

from pydantic import BaseModel


class Work(BaseModel):
    """作品基本信息"""
    id: int | None = None
    star_id: int
    code: str
    title: str | None = None
    release_date: str | None = None
    views: int | None = None
    likes: int | None = None
    resolution: str | None = None
    download_url: str | None = None
    cover_url: str | None = None
    cover_b64: str | None = None
    cover_path: str | None = None


class WorkWithMagnets(Work):
    """作品及其磁力链接"""
    magnets: list[dict] = []
