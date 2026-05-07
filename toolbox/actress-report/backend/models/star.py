from __future__ import annotations

from pydantic import BaseModel


class Star(BaseModel):
    """演员基本信息"""
    id: int | None = None
    name: str
    jp_name: str | None = None
    handle: str | None = None
    code: str | None = None
    type: str = "solo"
    note: str | None = None


class StarWithTitles(Star):
    """演员及其作品列表"""
    titles: list[dict] = []
    posts: list[dict] = []
