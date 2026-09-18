"""backend/routers/search.py — ijavtorrent catalog search

Searches the ijavtorrent catalog (WordPress-style `/?s=` endpoint) and
returns work cards enriched with local state: in-library flag and
per-actress followed flag. The same collection-stage filters as the sync
pipeline apply (scrapers/v2/filters.py), so VR / multi-star works never
show up here either — consistent with what a follow would actually import.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.routers.auth import require_auth
from backend.routers.stars import _load_config
from core import get_logger
from core.db.connection import _conn as _db_conn
from scrapers.v2.extractors import IJAV_BASE_URL, IJavTorrentExtractor
from scrapers.v2.filters import hidden_reason
from scrapers.v2.schemas import VideoItem

log = get_logger("search-router")
router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])

# In-memory TTL cache for the remote fetch + filter stage (the expensive
# part). Local enrichment (in_library / followed) runs per request so a
# follow or sync is reflected immediately.
_CACHE_TTL = 60.0
_items_cache: dict[str, tuple[float, list[VideoItem]]] = {}

# Code-like query: "SSIS-123", "ssis123", "229SCUTE-1575". WordPress search
# is fuzzy full-text, so for code-like queries we keep only exact code
# matches (dash-insensitive); keyword queries pass through in ijav's order.
_CODE_QUERY_RE = re.compile(r"^[A-Za-z0-9]{2,10}-?\d{2,5}$")


class SearchResultStar(BaseModel):
    name: str
    url: str
    followed: bool = False


class SearchResultItem(BaseModel):
    code: str
    title: str = ""
    release_date: str | None = None
    views: int | None = None
    likes: int | None = None
    cover_url: str | None = None
    resolution: str = ""
    size: str = ""
    seeds: int = 0
    in_library: bool = False
    stars: list[SearchResultStar] = []


class SearchResponse(BaseModel):
    query: str
    count: int
    items: list[SearchResultItem]


def _norm_code(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


async def _fetch_items(query: str) -> list[VideoItem]:
    """Fetch + extract + filter ijavtorrent search results (60s TTL cache)."""
    key = query.strip().lower()
    hit = _items_cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    from scrapers.v2.fetchers import HttpxFetcher

    url = f"{IJAV_BASE_URL}/?s={urllib.parse.quote(query.strip())}"
    async with HttpxFetcher() as fetcher:
        html = await fetcher.fetch(url)

    items = IJavTorrentExtractor().extract(html)

    config = _load_config()
    roster_names = [n for s in config.get("stars", []) for n in (s.get("name"), s.get("jp")) if n]

    kept: list[VideoItem] = []
    for it in items:
        # Same collection-stage rules as sync: drop VR / multi-star and
        # works with no magnet — they could never enter the library.
        if not it.magnets:
            continue
        own_names = [s.name for s in it.star_links]
        if hidden_reason(it, own_names=own_names, roster_names=roster_names):
            continue
        kept.append(it)

    if _CODE_QUERY_RE.match(query.strip()):
        want = _norm_code(query)
        kept = [it for it in kept if _norm_code(it.code) == want]

    _items_cache[key] = (time.time(), kept)
    return kept


def _library_codes(codes: list[str]) -> set[str]:
    if not codes:
        return set()
    placeholders = ", ".join("?" for _ in codes)
    conn = _db_conn()
    try:
        rows = conn.execute(
            f"SELECT code FROM titles WHERE code IN ({placeholders})", codes
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


@router.get("", response_model=SearchResponse)
async def search_titles(q: str = Query(..., min_length=2, max_length=100)) -> SearchResponse:
    """Search the ijavtorrent catalog by code or keyword."""
    query = q.strip()

    try:
        items = await _fetch_items(query)
    except Exception as exc:
        log.warning(f"search fetch failed for {query!r}: {exc}")
        raise HTTPException(status_code=502, detail=f"ijavtorrent 检索失败: {exc}") from exc

    config = _load_config()
    followed_urls = {s.get("star_page_url") for s in config.get("stars", [])}
    in_library = _library_codes([it.code for it in items])

    results = [
        SearchResultItem(
            code=it.code,
            title=it.title,
            release_date=it.release_date,
            views=it.views,
            likes=it.likes,
            cover_url=it.cover_url,
            resolution=it.magnets[0].resolution,
            size=it.magnets[0].size,
            seeds=it.magnets[0].seed,
            in_library=it.code in in_library,
            stars=[
                SearchResultStar(name=s.name, url=s.url, followed=s.url in followed_urls)
                for s in it.star_links
            ],
        )
        for it in items
    ]

    log.info(f"search {query!r}: {len(results)} results")
    return SearchResponse(query=query, count=len(results), items=results)
