"""backend/routers/search.py — ijavtorrent catalog search

Searches the ijavtorrent catalog (WordPress-style `/?s=` endpoint) and
returns work cards enriched with local state: in-library flag and
per-actress followed flag.

Keyword queries apply the same collection-stage filters as the sync
pipeline (scrapers/v2/filters.py), so VR / multi-star works stay
consistent with what a follow would actually import. Code-like queries
are exact-match lookups: the user explicitly asked for that work, so
filters are skipped and, when ijavtorrent has no match (its catalog has
been sparse since the 2026-08 loss), we fall back to the sukebei RSS
source — the same hybrid pattern as sync. sukebei items carry no cover
or actress links, so no follow button can be offered for them.
"""

from __future__ import annotations

import re
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.routers.auth import require_auth
from backend.routers.stars import _load_config
from core import get_logger
from core.db.connection import _conn as _db_conn
from scrapers.v2.extractors import IJAV_BASE_URL, IJavTorrentExtractor, SukebeiRssExtractor
from scrapers.v2.filters import hidden_reason
from scrapers.v2.schemas import VideoItem
from scrapers.v2.tasks.sync_titles import SUKEBEI_RSS_URL

log = get_logger("search-router")
router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])

# In-memory TTL cache for the remote fetch + filter stage (the expensive
# part). Local enrichment (in_library / followed) runs per request so a
# follow or sync is reflected immediately.
_CACHE_TTL = 60.0
_items_cache: dict[str, tuple[float, list[tuple[VideoItem, str]]]] = {}

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
    source: str = "ijav"  # "ijav" (rich metadata) or "sukebei" (fallback, no cover/actress)
    stars: list[SearchResultStar] = []


class SearchResponse(BaseModel):
    query: str
    count: int
    items: list[SearchResultItem]


def _norm_code(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


async def _ijav_items(fetcher, query: str) -> list[VideoItem]:
    url = f"{IJAV_BASE_URL}/?s={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url)
    return IJavTorrentExtractor().extract(html)


async def _sukebei_exact(fetcher, query: str) -> list[VideoItem]:
    """sukebei RSS fallback for code lookups ijavtorrent can't serve."""
    url = SUKEBEI_RSS_URL.format(q=urllib.parse.quote(query))
    rss = await fetcher.fetch(url)
    if not rss.rstrip().endswith("</rss>"):
        log.warning(f"sukebei fallback: truncated rss for {query!r}")
        return []
    want = _norm_code(query)
    return [
        it for it in SukebeiRssExtractor().extract(rss)
        if it.magnets and _norm_code(it.code) == want
    ]


async def _fetch_items(query: str) -> list[tuple[VideoItem, str]]:
    """Fetch + extract + filter search results (60s TTL cache).

    Returns (item, source) pairs; source is "ijav" or "sukebei".
    """
    key = query.strip().lower()
    hit = _items_cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    from scrapers.v2.fetchers import HttpxFetcher

    code_query = bool(_CODE_QUERY_RE.match(query.strip()))
    want = _norm_code(query)

    async with HttpxFetcher() as fetcher:
        items = await _ijav_items(fetcher, query.strip())

        kept: list[tuple[VideoItem, str]] = []
        if code_query:
            # Exact-match lookup: explicit user intent — skip the collection
            # filters (they exist to keep the catalog clean, not to hide a
            # work the user asked for by code); magnets still required.
            kept = [
                (it, "ijav") for it in items
                if it.magnets and _norm_code(it.code) == want
            ]
            if not kept:
                try:
                    kept = [(it, "sukebei") for it in await _sukebei_exact(fetcher, query.strip())]
                except Exception as exc:
                    log.warning(f"sukebei fallback failed for {query!r}: {exc}")
        else:
            config = _load_config()
            roster_names = [
                n for s in config.get("stars", []) for n in (s.get("name"), s.get("jp")) if n
            ]
            for it in items:
                # Same collection-stage rules as sync: drop VR / multi-star
                # and works with no magnet — they could never enter the library.
                if not it.magnets:
                    continue
                own_names = [s.name for s in it.star_links]
                if hidden_reason(it, own_names=own_names, roster_names=roster_names):
                    continue
                kept.append((it, "ijav"))

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
    """Search the catalog by code or keyword (ijavtorrent, sukebei fallback)."""
    query = q.strip()

    try:
        items = await _fetch_items(query)
    except Exception as exc:
        log.warning(f"search fetch failed for {query!r}: {exc}")
        raise HTTPException(status_code=502, detail=f"检索失败: {exc}") from exc

    config = _load_config()
    followed_urls = {s.get("star_page_url") for s in config.get("stars", [])}
    in_library = _library_codes([it.code for it, _ in items])

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
            source=source,
            stars=[
                SearchResultStar(name=s.name, url=s.url, followed=s.url in followed_urls)
                for s in it.star_links
            ],
        )
        for it, source in items
    ]

    log.info(f"search {query!r}: {len(results)} results")
    return SearchResponse(query=query, count=len(results), items=results)
