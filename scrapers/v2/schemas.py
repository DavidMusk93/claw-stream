"""scrapers/v2/schemas.py — Crawler data structure definitions

All extraction results are uniformly validated with Pydantic to ensure type safety.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MagnetCandidate(BaseModel):
    """Single magnet link candidate"""

    magnet: str
    resolution: str = ""
    size: str = ""           # Raw size string, e.g. "5.2GB"
    seed: int = 0
    leech: int = 0
    is_hhd800: bool = False  # Whether it is an hhd800.com@ HD source


class VideoItem(BaseModel):
    """ijavtorrent work card"""

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
    """Best magnet link after scoring"""

    magnet: str = ""
    resolution: str = ""
    size: str = ""
    all_magnet_urls: list[str] = []


class StarConfig(BaseModel):
    """Single star config in config.json"""

    name: str
    code: str
    handle: str = ""
    star_page_url: str = ""
    type: str = "solo"
    note: str = ""
    jp: str = ""            # Romaji name (present in config.json)
    sync_query: str = ""    # Optional override for the sukebei RSS search query
