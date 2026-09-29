"""backend/routers/search.py — ijavtorrent catalog search

Searches the ijavtorrent catalog (`/?searchTerm=` endpoint — the older
`/?s=` param silently returns the homepage since the 2026-09 redesign) and
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

Name queries also hit ijavtorrent's server-side actress directory search
(`/actresses?searchTerm=`), so an actress is followable (with her profile
image) even when the video search returns nothing for the name; the
offline-crawled local index (data/actress_index.json) is the fallback.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.routers.auth import require_auth
from backend.routers.stars import _load_config
from core import get_logger
from core.db.connection import _conn as _db_conn
from scrapers.v2.extractors import (
    IJAV_BASE_URL,
    IJavTorrentExtractor,
    SukebeiRssExtractor,
    parse_actress_directory,
)
from scrapers.v2.filters import hidden_reason
from scrapers.v2.schemas import VideoItem
from scrapers.v2.sinks import TitleSyncSink
from scrapers.v2.tasks.sync_titles import SUKEBEI_RSS_URL

log = get_logger("search-router")
router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])

# In-memory TTL cache for the remote fetch + filter stage (the expensive
# part). Local enrichment (in_library / followed) runs per request so a
# follow or sync is reflected immediately.
_CACHE_TTL = 60.0
_items_cache: dict[str, tuple[float, list[tuple[VideoItem, str]], list[dict]]] = {}

# Code-like query: "SSIS-123", "ssis123", "229SCUTE-1575". WordPress search
# is fuzzy full-text, so for code-like queries we keep only exact code
# matches (dash-insensitive); keyword queries pass through in ijav's order.
_CODE_QUERY_RE = re.compile(r"^[A-Za-z0-9]{2,10}-?\d{2,5}$")


class SearchResultStar(BaseModel):
    name: str
    url: str
    followed: bool = False


class SearchResultActress(SearchResultStar):
    """Directory match from ijavtorrent's actress search (or the local
    actress index fallback) — followable even when the video search
    returns nothing for the name."""

    image: str = ""


class SearchResultMagnet(BaseModel):
    magnet: str
    resolution: str = ""
    size: str = ""
    seeds: int = 0
    is_hd: bool = False  # hhd800.com high-quality source


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
    magnets: list[SearchResultMagnet] = []  # all candidates, best first (sync scoring)


class SearchResponse(BaseModel):
    query: str
    count: int
    items: list[SearchResultItem]
    actresses: list[SearchResultActress] = []  # name matches from the actress directory


def _norm_code(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


# Local actress directory index — offline fallback for the live
# /actresses?searchTerm= search (ijavtorrent down or empty). Crawled by
# scripts/build_actress_index.py; lazy-loaded, mtime-cached.
_ACTRESS_INDEX_PATH = Path(__file__).resolve().parents[2] / "data" / "actress_index.json"
_actress_index: tuple[float, list[dict]] | None = None


def _actress_matches(query: str, limit: int = 12) -> list[dict]:
    global _actress_index
    q = query.strip().lower()
    if len(q) < 2:
        return []
    try:
        mtime = _ACTRESS_INDEX_PATH.stat().st_mtime
    except OSError:
        return []
    if _actress_index is None or _actress_index[0] != mtime:
        try:
            data = json.loads(_ACTRESS_INDEX_PATH.read_text())
            _actress_index = (mtime, data.get("actresses", []))
        except Exception as exc:
            log.warning(f"actress index unreadable: {exc}")
            _actress_index = (mtime, [])
    entries = _actress_index[1]
    # Prefix matches rank above plain substring matches; ties break by
    # movie count so the famous names surface first.
    starts = sorted(
        (a for a in entries if a["name"].lower().startswith(q)),
        key=lambda a: -a.get("movies", 0),
    )
    contains = sorted(
        (a for a in entries if q in a["name"].lower() and not a["name"].lower().startswith(q)),
        key=lambda a: -a.get("movies", 0),
    )
    return (starts + contains)[:limit]


async def _ijav_items(fetcher, query: str) -> list[VideoItem]:
    # ?searchTerm= is ijavtorrent's search param since the 2026-09 redesign;
    # the old ?s= silently returns the homepage for every query.
    url = f"{IJAV_BASE_URL}/?searchTerm={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url)
    return IJavTorrentExtractor().extract(html)


async def _ijav_actresses(fetcher, query: str) -> list[dict]:
    """Live actress directory search; empty list on any failure (the caller
    falls back to the offline-crawled local index)."""
    try:
        url = f"{IJAV_BASE_URL}/actresses?searchTerm={urllib.parse.quote(query)}"
        html = await fetcher.fetch(url)
        return parse_actress_directory(html)
    except Exception as exc:
        log.warning(f"actress directory search failed for {query!r}: {exc}")
        return []


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


async def _fetch_items(query: str) -> tuple[list[tuple[VideoItem, str]], list[dict]]:
    """Fetch + extract + filter search results (60s TTL cache).

    Returns (work items, directory actresses); item source is "ijav" or
    "sukebei". Directory actresses come from ijavtorrent's live actress
    search — empty for code-like queries and on fetch failure.
    """
    key = query.strip().lower()
    hit = _items_cache.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1], hit[2]

    from scrapers.v2.fetchers import HttpxFetcher

    code_query = bool(_CODE_QUERY_RE.match(query.strip()))
    want = _norm_code(query)

    async with HttpxFetcher() as fetcher:
        # A code-like query can never be an actress name — skip the
        # directory round-trip.
        actresses = [] if code_query else await _ijav_actresses(fetcher, query.strip())
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

    _items_cache[key] = (time.time(), kept, actresses)
    return kept, actresses


def _library_codes(codes: list[str]) -> set[str]:
    if not codes:
        return set()
    placeholders = ", ".join("%s" for _ in codes)
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
        items, directory = await _fetch_items(query)
    except Exception as exc:
        log.warning(f"search fetch failed for {query!r}: {exc}")
        raise HTTPException(status_code=502, detail=f"检索失败: {exc}") from exc

    config = _load_config()
    followed_urls = {s.get("star_page_url") for s in config.get("stars", [])}
    in_library = _library_codes([it.code for it, _ in items])

    results = []
    for it, source in items:
        # Same best-magnet selection as the sync pipeline (hhd800 bonus,
        # resolution tier, seeds, size) — not raw extractor order.
        scored = sorted(
            it.magnets, key=lambda m: TitleSyncSink._score_magnet(m), reverse=True
        )
        best = scored[0]
        results.append(
            SearchResultItem(
                code=it.code,
                title=it.title,
                release_date=it.release_date,
                views=it.views,
                likes=it.likes,
                cover_url=it.cover_url,
                resolution=best.resolution,
                size=best.size,
                seeds=best.seed,
                in_library=it.code in in_library,
                source=source,
                stars=[
                    SearchResultStar(name=s.name, url=s.url, followed=s.url in followed_urls)
                    for s in it.star_links
                ],
                magnets=[
                    SearchResultMagnet(
                        magnet=m.magnet,
                        resolution=m.resolution,
                        size=m.size,
                        seeds=m.seed,
                        is_hd=m.is_hhd800,
                    )
                    for m in scored
                ],
            )
        )

    log.info(f"search {query!r}: {len(results)} results")
    # Live directory search first; the offline-crawled local index is the
    # fallback when ijavtorrent returned nothing (down or no match).
    matches = (directory or _actress_matches(query))[:12]
    actresses = [
        SearchResultActress(
            name=a["name"],
            url=a["url"],
            image=a.get("image", ""),
            followed=a["url"] in followed_urls,
        )
        for a in matches
    ]
    return SearchResponse(query=query, count=len(results), items=results, actresses=actresses)
