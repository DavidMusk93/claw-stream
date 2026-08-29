"""scrapers/v2/tasks/sync_titles.py — Sync actor latest titles

Diff-Sync architecture (first principle: update as fast as possible):
1. Batch upsert stars + preload all existing title codes (memory set)
2. Per-star pipeline, all stars overlapped: fetch (pure HTTP, no browser) →
   in-memory diff (keep only new works) → incremental cover download (new
   works only, shared global semaphore) → incremental write (per-star UPSERT
   through the serial DuckDB write queue). A star's covers/writes hide under
   the remaining stars' fetch time.

Hybrid source: **ijavtorrent is the primary source** (actress pages carry the
rich metadata: retail dates, views, likes, cover_url, hhd800-tagged magnets).
**sukebei.nyaa.si RSS is the supplement/correction**: its per-star search
covers titles missing from ijavtorrent's listing (ijav lost most of its
catalog in 2026-08 and, even after recovery, shows sparse/capped actress
listings with no pagination, e.g. JULIA 61 cards vs 201 in DB) and adds
extra magnet candidates. Merge rule: ijavtorrent metadata wins, magnet
candidates are unioned (deduped by magnet), RSS-only codes are appended.

Failure semantics: a star fails only when BOTH sources fail. One source
failing degrades to the other with a loud warning — never a silent "no new
titles". If every star fails, run() raises so callers report a sync error.

Truncated-response semantics: an ijavtorrent page without a closing </html>
tag or parsing to 0 cards, and an RSS body without a closing </rss> tag or
with unparseable XML, are retried before giving up (MAX_FETCH_ATTEMPTS).
There is no DB-count floor for ijavtorrent pages: listings are legitimately
sparse since the 2026-08 catalog loss, so a low card count is not proof of
a truncated transfer.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Awaitable, Callable

import httpx

from core import get_logger
from core import db
from core.db.write_queue import db_write
from scrapers.v2.fetchers import HttpxFetcher
from scrapers.v2.extractors import IJavTorrentExtractor, SukebeiRssExtractor
from scrapers.v2.sinks import TitleSyncSink
from scrapers.v2.schemas import VideoItem, StarConfig
from scrapers.v2.cover_utils import download_covers_batch

log = get_logger("sync-titles")

MAX_NEW_TITLES = 20  # Max new titles processed per sync (prevent stars with too many works from overwhelming sync)

MAX_FETCH_ATTEMPTS = 3  # Retries per star before declaring the fetch failed
FETCH_RETRY_DELAYS = (1.0, 2.0)  # Backoff between attempts (seconds); tests patch to zeros
RATE_LIMIT_RETRY_DELAY = 10.0  # Backoff after an HTTP 429 from sukebei
RSS_MAX_CONCURRENCY = 4  # sukebei rate-limits aggressive bursts (429); retries absorb the occasional 429
RSS_REQUEST_INTERVAL = 0.3  # Min seconds between RSS requests (held under the semaphore)
COVER_DOWNLOAD_CONCURRENCY = 16  # Parallel cover downloads; DMM CDN / ijav images tolerate this

SUKEBEI_RSS_URL = "https://sukebei.nyaa.si/?page=rss&q={q}&c=0_0&f=0&s=id&o=desc"


class IncompletePageError(Exception):
    """Page fetched with HTTP 200 but looks truncated (stream cut or partial card list)."""


def _sort_key(item: VideoItem) -> str:
    """Key for sorting by date descending. Date format is dd/mm/YYYY."""
    if item.release_date:
        parts = item.release_date.split("/")
        if len(parts) == 3:
            # parts[0]=dd, parts[1]=mm, parts[2]=YYYY → YYYYMMDD
            return parts[2] + parts[1].zfill(2) + parts[0].zfill(2)
    return "00000000"


def _dedup(items: list[VideoItem]) -> list[VideoItem]:
    """Deduplicate by code, sort by date descending"""
    seen: set[str] = set()
    unique: list[VideoItem] = []
    for it in items:
        if it.code not in seen:
            seen.add(it.code)
            unique.append(it)
    unique.sort(key=_sort_key, reverse=True)
    return unique


async def fetch_star_rss(
    fetcher: HttpxFetcher,
    star: StarConfig,
    semaphore: asyncio.Semaphore,
) -> list[VideoItem]:
    """Fetch a single star's sukebei RSS searches and parse out VideoItems.

    All query variants (sync_query, name, jp) are fetched and their results
    merged by code — uploaders tag different spellings across torrents, so
    the first non-empty query is NOT good enough (e.g. romaji-only results
    would miss titles tagged with just the Japanese name).

    Raises on persistent failure: an unreachable or empty result is an
    error, not an empty title list. Callers must handle the exception
    explicitly.
    """
    queries: list[str] = []
    for q in (star.sync_query, star.name, star.jp):
        if q and q not in queries:
            queries.append(q)

    last_err: Exception | None = None
    rate_limited = False
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        merged: dict[str, VideoItem] = {}
        for query in queries:
            url = SUKEBEI_RSS_URL.format(q=urllib.parse.quote(query))
            try:
                async with semaphore:
                    rss = await fetcher.fetch(url)
                    # Pace requests: sukebei answers bursts with HTTP 429
                    await asyncio.sleep(RSS_REQUEST_INTERVAL)
            except Exception as e:
                last_err = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    rate_limited = True
                    log.warning(f"rate limited 429 (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}")
                    break  # Long back-off before the next attempt, not the next query
                log.warning(f"rss fetch failed (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}: {type(e).__name__}: {e}")
                continue
            if not rss.rstrip().endswith("</rss>"):
                last_err = IncompletePageError("missing closing </rss> tag")
                log.warning(f"truncated rss (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}: no </rss>, len={len(rss)}")
                continue
            try:
                items = SukebeiRssExtractor().extract(
                    rss, star_names={star.name, star.jp, star.sync_query}
                )
            except ET.ParseError as e:
                last_err = IncompletePageError(f"unparseable RSS XML: {e}")
                log.warning(f"truncated rss (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}: {e}")
                continue
            for it in items:
                if it.code in merged:
                    _merge_into(merged[it.code], it)
                else:
                    merged[it.code] = it
            log.info(f"{star.name}: q={query!r} → {len(items)} titles")
        if merged:
            items = _dedup(list(merged.values()))
            log.info(f"{star.name}: {len(items)} titles after merging {len(queries)} queries")
            return items
        if attempt < MAX_FETCH_ATTEMPTS:
            if rate_limited:
                rate_limited = False
                await asyncio.sleep(RATE_LIMIT_RETRY_DELAY)
            else:
                await asyncio.sleep(FETCH_RETRY_DELAYS[attempt - 1])
    if last_err is None:
        last_err = IncompletePageError(f"0 usable items for all queries: {queries}")
    log.error(f"rss fetch failed: {star.name}: {type(last_err).__name__}: {last_err}")
    raise last_err


def _merge_into(dst: VideoItem, src: VideoItem) -> None:
    """Merge same-code items from different query variants (dedupe by magnet).

    dst's metadata wins: for the hybrid source merge dst is the ijavtorrent
    item, whose retail release_date / views / cover_url are canonical.
    """
    known = set(dst.all_magnet_urls)
    for c in src.magnets:
        if c.magnet not in known:
            known.add(c.magnet)
            dst.magnets.append(c)
            dst.all_magnet_urls.append(c.magnet)
    dst.likes = max(dst.likes or 0, src.likes or 0) or None
    if not dst.release_date:
        dst.release_date = src.release_date
    if not dst.cover_url:
        dst.cover_url = src.cover_url


async def fetch_star_page(
    fetcher: HttpxFetcher,
    star: StarConfig,
    semaphore: asyncio.Semaphore,
) -> list[VideoItem]:
    """Fetch a single star's ijavtorrent actress page (the primary source).

    Retries up to MAX_FETCH_ATTEMPTS times when the page looks truncated
    (no closing </html> tag) or parses to 0 cards (layout change / empty
    page). There is deliberately no DB-count floor: since the 2026-08
    catalog loss, ijavtorrent listings are legitimately sparse (no
    pagination, e.g. JULIA 61 cards vs 201 in DB), so a low card count is
    not proof of a truncated transfer — the sukebei supplement compensates
    for catalog gaps.

    Stars without a star_page_url (added before ijavtorrent recovered)
    return [] and are served by the RSS supplement alone.

    Raises on persistent failure: an unreachable or truncated page is an
    error, not an empty title list. Callers must handle the exception
    explicitly.
    """
    if not star.star_page_url:
        raise IncompletePageError("no ijavtorrent star_page_url configured")

    last_err: Exception | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            async with semaphore:
                html = await fetcher.fetch(star.star_page_url)
        except Exception as e:
            last_err = e
            log.warning(f"ijav fetch failed (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name}: {type(e).__name__}: {e}")
        else:
            if not html.rstrip().endswith("</html>"):
                last_err = IncompletePageError("missing closing </html> tag")
                log.warning(f"truncated page (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name}: no </html>, len={len(html)}")
            else:
                items = _dedup(IJavTorrentExtractor().extract(html))
                if items:
                    log.info(f"{star.name}: ijav → {len(items)} titles")
                    return items
                last_err = IncompletePageError("page fetched but 0 titles parsed (layout change or empty page?)")
                log.warning(f"empty page (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name}: 0 titles parsed")
        if attempt < MAX_FETCH_ATTEMPTS:
            await asyncio.sleep(FETCH_RETRY_DELAYS[attempt - 1])
    log.error(f"ijav fetch failed: {star.name}: {type(last_err).__name__}: {last_err}")
    raise last_err


def merge_sources(
    primary: list[VideoItem],
    supplement: list[VideoItem],
    star_name: str = "",
) -> list[VideoItem]:
    """Merge ijavtorrent (primary) with sukebei RSS (supplement) by code.

    Same code → magnet candidates unioned (ijav metadata wins); RSS-only
    codes appended — they correct ijavtorrent's catalog gaps.
    """
    merged: dict[str, VideoItem] = {it.code: it for it in primary}
    added = 0
    enriched = 0
    for it in supplement:
        dst = merged.get(it.code)
        if dst is None:
            merged[it.code] = it
            added += 1
        else:
            before = len(dst.magnets)
            _merge_into(dst, it)
            if len(dst.magnets) > before:
                enriched += 1
    items = _dedup(list(merged.values()))
    log.info(f"{star_name}: merged {len(primary)} ijav + {len(supplement)} rss → {len(items)} titles ({added} rss-only, {enriched} enriched)")
    return items


def _drop_multi_star(items: list[VideoItem], star_name: str = "") -> list[VideoItem]:
    """Filter out multi-star (共演/omnibus) titles.

    IJavTorrentExtractor counts the /actress/ links on each card into
    star_count — solo works have exactly 1. RSS-supplement items have
    star_count=0 (unknown) and pass through; omnibus compilations rarely
    mention the star in the RSS title anyway, so the noise filter already
    drops most of them there.
    """
    solo = [it for it in items if it.star_count <= 1]
    dropped = len(items) - len(solo)
    if dropped:
        log.info(f"{star_name}: filtered {dropped} multi-star titles")
    return solo


async def fetch_star(
    fetcher: HttpxFetcher,
    star: StarConfig,
    ijav_sem: asyncio.Semaphore,
    rss_sem: asyncio.Semaphore,
) -> list[VideoItem]:
    """Hybrid per-star fetch: ijavtorrent primary + sukebei RSS supplement.

    One source failing degrades to the other with a loud warning; the star
    fails only when BOTH sources fail. Multi-star (共演/omnibus) titles are
    filtered out of the result.
    """
    ijav_res, rss_res = await asyncio.gather(
        fetch_star_page(fetcher, star, ijav_sem),
        fetch_star_rss(fetcher, star, rss_sem),
        return_exceptions=True,
    )
    ijav_err = ijav_res if isinstance(ijav_res, BaseException) else None
    rss_err = rss_res if isinstance(rss_res, BaseException) else None
    if ijav_err is not None and rss_err is not None:
        raise IncompletePageError(f"both sources failed: ijav={ijav_err}; rss={rss_err}")
    if ijav_err is not None:
        log.warning(f"{star.name}: ijavtorrent unavailable ({ijav_err}), sukebei-only degraded sync")
        return _drop_multi_star(rss_res, star.name)
    if rss_err is not None:
        log.warning(f"{star.name}: sukebei rss unavailable ({rss_err}), ijavtorrent-only degraded sync")
        return _drop_multi_star(ijav_res, star.name)
    return _drop_multi_star(merge_sources(ijav_res, rss_res, star.name), star.name)


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def run(
    config_path: str = "config.json",
    fetch_concurrency: int = 8,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Main entry: read config, concurrently sync all stars.

    ``on_progress`` (optional) is awaited with a phase dict at each stage so
    callers can stream live progress (e.g. SSE ``sync.progress`` events):
    ``prepare`` → per-star ``fetch``/``covers``/``write`` (pipelined — phases
    from different stars interleave, counters are cumulative).

    Returns {"results": per-star sync summaries, "failed": per-star fetch
    failures}. Raises RuntimeError when every star page fetch fails — a total
    outage must surface as a sync error, not as "0 new titles".
    """
    t_total = time.perf_counter()

    async def _emit(phase: str, **kw: Any) -> None:
        if on_progress is not None:
            try:
                await on_progress({"phase": phase, **kw})
            except Exception:
                log.exception(f"progress callback failed (phase={phase})")

    await _emit("prepare", detail="Loading star list and existing titles")

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    stars = [StarConfig(**s) for s in raw.get("stars", [])]
    log.info(f"syncing {len(stars)} stars, fetch_concurrency={fetch_concurrency}")

    # 1. Batch upsert stars (single write-queue round trip), build code → star_id map
    t0 = time.perf_counter()

    star_id_map = await db_write(db.upsert_stars, [
        {"name": s.name, "handle": s.handle, "code": s.code} for s in stars
    ])
    t1 = time.perf_counter()
    log.info(f"[timing] upsert stars: {(t1 - t0) * 1000:.1f}ms")

    # 2. Preload existing title codes and missing-metadata codes into memory sets
    t0 = time.perf_counter()
    existing_codes = await db_write(db.load_all_title_codes)
    missing_codes = await db_write(db.load_title_codes_missing_metadata)
    t1 = time.perf_counter()
    log.info(f"[timing] load existing codes: {(t1 - t0) * 1000:.1f}ms | count={len(existing_codes)} | missing={len(missing_codes)}")

    # 3-5. Per-star pipeline: fetch → diff → covers → write, overlapped across
    # stars. A star's covers download and its rows write while later stars are
    # still fetching, hiding most cover/write time under fetch time. The
    # DuckDB write queue serializes all writes anyway, so per-star writes
    # don't change write-side safety.
    t0 = time.perf_counter()
    fetched = 0
    failed: list[dict[str, str]] = []
    clean: list[dict] = []
    total_new = 0
    total_updated = 0

    async with HttpxFetcher() as fetcher:
        ijav_sem = asyncio.Semaphore(fetch_concurrency)
        rss_sem = asyncio.Semaphore(min(fetch_concurrency, RSS_MAX_CONCURRENCY))
        cover_sem = asyncio.Semaphore(COVER_DOWNLOAD_CONCURRENCY)

        async def _process_star(star: StarConfig) -> None:
            nonlocal fetched, total_new, total_updated

            # Fetch: ijavtorrent primary + sukebei RSS supplement
            ok, count, err = True, 0, None
            try:
                items = await fetch_star(fetcher, star, ijav_sem, rss_sem)
                count = len(items)
            except Exception as e:
                ok, err = False, f"{type(e).__name__}: {e}"[:200]
                fetched += 1
                await _emit("fetch", star=star.name, done=fetched, total=len(stars),
                            ok=ok, titles=count, error=err)
                log.error(f"fetch exception for {star.name}: {e}")
                failed.append({"name": star.name, "error": f"{type(e).__name__}: {e}"})
                return
            fetched += 1
            await _emit("fetch", star=star.name, done=fetched, total=len(stars),
                        ok=ok, titles=count)

            # Diff: new works + existing works with missing metadata
            star_id = star_id_map[star.code]
            new_items = [it for it in items if (star_id, it.code) not in existing_codes]
            if len(new_items) > MAX_NEW_TITLES:
                log.info(f"{star.name}: {len(new_items)} new, limiting to {MAX_NEW_TITLES}")
                new_items = new_items[:MAX_NEW_TITLES]
            backfill_items = [
                it for it in items
                if (star_id, it.code) in missing_codes and (star_id, it.code) in existing_codes
            ]
            sync_items = new_items + backfill_items
            if not sync_items:
                log.info(f"{star.name}: no new titles")
                return
            log.info(f"{star.name}: {len(new_items)} new, {len(backfill_items)} backfill")

            # Covers for this star (shared global cap + shared HTTP client),
            # then write — both overlap with other stars' fetches.
            try:
                cover_items = [(it.code, it.cover_url or "") for it in sync_items]
                cover_map = await download_covers_batch(
                    cover_items, concurrency=COVER_DOWNLOAD_CONCURRENCY,
                    sem=cover_sem, fetcher=fetcher,
                )
                await _emit("covers", star=star.name, count=len(cover_items),
                            downloaded=len(cover_map))

                sink = TitleSyncSink(star_id=star_id, star_code=star.code, star_name=star.name)
                new_codes = {it.code for it in new_items}
                batch_result = await sink.write_batch(sync_items, new_codes, cover_map)
            except Exception as e:
                log.error(f"post-fetch failure for {star.name}: {e}")
                failed.append({"name": star.name, "error": f"{type(e).__name__}: {e}"})
                return
            total_new += batch_result["new"]
            total_updated += batch_result["updated"]
            log.info(f"done: {star.name}: {batch_result['new']} new, {batch_result['updated']} backfill")
            clean.append({"name": star.name, "titles": sync_items, "count": batch_result["new"]})
            await _emit("write", star=star.name, done=len(clean), total=len(stars),
                        new=batch_result["new"])

        await _emit("fetch", done=0, total=len(stars))
        await asyncio.gather(*[_process_star(star) for star in stars])
    t1 = time.perf_counter()
    log.info(f"[timing] pipeline (fetch+covers+write): {(t1 - t0) * 1000:.1f}ms")

    # A total fetch failure means nothing was synced at all — surface it as an
    # error instead of reporting a fake "0 new titles" success.
    if stars and len(failed) == len(stars):
        raise RuntimeError(
            f"all {len(stars)} star fetches failed: {failed[0]['error']}"
        )
    if failed:
        log.warning(f"{len(failed)}/{len(stars)} star fetches failed: "
                    f"{', '.join(f['name'] for f in failed)}")

    # Statistics
    t0 = time.perf_counter()
    rows = await db_write(_query_stats)
    t1 = time.perf_counter()
    log.info(f"[timing] stats query: {(t1 - t0) * 1000:.1f}ms")

    total = 0
    for _, name, count in rows:
        log.info(f"{name}: {count} titles")
        total += count
    log.info(f"sync complete: {total_new} new, {total_updated} backfill, {total} total titles, {len(failed)} fetch failed | total elapsed={(time.perf_counter() - t_total) * 1000:.1f}ms")

    return {"results": clean, "failed": failed}


def _query_stats(conn=None):
    """Count titles per star (for db_write queue invocation)."""
    managed, should_close = db._managed_conn(conn)
    try:
        rows = managed.execute("""
            SELECT s.code, s.name, COUNT(t.id) as title_count
            FROM stars s
            LEFT JOIN titles t ON t.star_id = s.id
            GROUP BY s.id, s.code, s.name
            ORDER BY s.name
        """).fetchall()
        if should_close:
            managed.commit()
        return rows
    finally:
        if should_close:
            managed.close()


async def sync_star(
    fetcher: HttpxFetcher,
    star: StarConfig,
) -> dict:
    """Sync titles for a single star (background sync after adding a new actor)."""
    t0 = time.perf_counter()
    star_id = await db_write(
        db.upsert_star,
        name=star.name,
        handle=star.handle,
        code=star.code,
    )

    existing_codes = await db_write(db.load_all_title_codes)
    items = await fetch_star(
        fetcher, star, asyncio.Semaphore(1), asyncio.Semaphore(1)
    )
    if not items:
        return {"name": star.name, "count": 0, "titles": []}

    new_items = [it for it in items if (star_id, it.code) not in existing_codes]

    if len(new_items) > MAX_NEW_TITLES:
        new_items = new_items[:MAX_NEW_TITLES]

    if not new_items:
        return {"name": star.name, "count": 0, "titles": []}

    cover_items = [(it.code, it.cover_url or "") for it in new_items]
    cover_map = await download_covers_batch(cover_items, concurrency=COVER_DOWNLOAD_CONCURRENCY)

    sink = TitleSyncSink(star_id=star_id, star_code=star.code, star_name=star.name)
    new_codes_set = {it.code for it in new_items}
    batch_result = await sink.write_batch(new_items, new_codes_set, cover_map)

    elapsed = (time.perf_counter() - t0) * 1000
    log.info(f"[timing] sync_star {star.name}: {elapsed:.1f}ms | new={batch_result['new']}")
    return {
        "name": star.name,
        "titles": new_items,
        "count": batch_result["new"],
    }


if __name__ == "__main__":
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    asyncio.run(run(config))
