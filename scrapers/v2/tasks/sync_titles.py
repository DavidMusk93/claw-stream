"""scrapers/v2/tasks/sync_titles.py — Sync actor latest titles

Diff-Sync architecture (first principle: update as fast as possible):
1. Batch upsert stars + preload all existing title codes (memory set)
2. Pure HTTP concurrent fetching of star pages (HttpxFetcher, no browser overhead)
3. In-memory diff: keep only new works
4. Incremental cover download: only download covers for new works
5. Incremental database write: only INSERT new works
"""

from __future__ import annotations

import asyncio
import json
import time

from core import get_logger
from core import db
from core.db.write_queue import db_write
from scrapers.v2.fetchers import HttpxFetcher
from scrapers.v2.extractors import IJavTorrentExtractor
from scrapers.v2.sinks import TitleSyncSink
from scrapers.v2.schemas import VideoItem, StarConfig
from scrapers.v2.cover_utils import download_covers_batch

log = get_logger("sync-titles")

MAX_NEW_TITLES = 20  # Max new titles processed per sync (prevent stars with too many works from overwhelming sync)


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


async def fetch_star_page(
    fetcher: HttpxFetcher,
    star: StarConfig,
    semaphore: asyncio.Semaphore,
) -> list[VideoItem]:
    """Fetch a single star's page and parse out a list of VideoItems."""
    async with semaphore:
        try:
            html = await fetcher.fetch(star.star_page_url)
        except Exception as e:
            log.error(f"fetch failed: {star.name}: {type(e).__name__}: {e}")
            return []

    extractor = IJavTorrentExtractor()
    raw_items = extractor.extract(html)
    items = _dedup(raw_items)
    log.info(f"{star.name}: {len(raw_items)} raw → {len(items)} dedup")
    return items


async def run(config_path: str = "config.json", fetch_concurrency: int = 8) -> list[dict]:
    """Main entry: read config, concurrently sync all stars"""
    t_total = time.perf_counter()

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    stars = [StarConfig(**s) for s in raw.get("stars", [])]
    log.info(f"syncing {len(stars)} stars, fetch_concurrency={fetch_concurrency}")

    # 1. Batch upsert stars and build code → star_id mapping
    t0 = time.perf_counter()
    star_id_map: dict[str, int] = {}
    for star in stars:
        star_id = await db_write(
            db.upsert_star,
            name=star.name,
            handle=star.handle,
            code=star.code,
        )
        star_id_map[star.code] = star_id
    t1 = time.perf_counter()
    log.info(f"[timing] upsert stars: {(t1 - t0) * 1000:.1f}ms")

    # 2. Preload all existing title codes into memory set
    t0 = time.perf_counter()
    existing_codes = await db_write(db.load_all_title_codes)
    t1 = time.perf_counter()
    log.info(f"[timing] load existing codes: {(t1 - t0) * 1000:.1f}ms | count={len(existing_codes)}")

    # 3. Concurrently fetch all star pages (pure HTTP, no browser overhead)
    t0 = time.perf_counter()
    async with HttpxFetcher() as fetcher:
        sem = asyncio.Semaphore(fetch_concurrency)
        page_results = await asyncio.gather(
            *[fetch_star_page(fetcher, star, sem) for star in stars],
            return_exceptions=True,
        )
    t1 = time.perf_counter()
    log.info(f"[timing] fetch star pages: {(t1 - t0) * 1000:.1f}ms")

    # 4. Diff: keep only new works, skip existing ones
    sync_batches: list[tuple[int, str, list[VideoItem]]] = []
    all_new_items: list[VideoItem] = []

    for star, page_result in zip(stars, page_results):
        if isinstance(page_result, Exception):
            log.error(f"page fetch exception for {star.name}: {page_result}")
            continue

        star_id = star_id_map[star.code]
        items = page_result

        new_items = [it for it in items if (star_id, it.code) not in existing_codes]
        if len(new_items) > MAX_NEW_TITLES:
            log.info(f"{star.name}: {len(new_items)} new, limiting to {MAX_NEW_TITLES}")
            new_items = new_items[:MAX_NEW_TITLES]

        if new_items:
            sync_batches.append((star_id, star.name, new_items))
            all_new_items.extend(new_items)
            log.info(f"{star.name}: {len(new_items)} new titles to sync")
        else:
            log.info(f"{star.name}: no new titles")

    # 5. Incremental cover download: only download covers for new works
    cover_map: dict[str, str] = {}
    if all_new_items:
        t0 = time.perf_counter()
        cover_items = [(it.code, it.cover_url or "") for it in all_new_items]
        log.info(f"downloading {len(cover_items)} new covers in batch...")
        cover_map = await download_covers_batch(cover_items, concurrency=8)
        t1 = time.perf_counter()
        log.info(f"[timing] download covers: {(t1 - t0) * 1000:.1f}ms | downloaded={len(cover_map)}")

    # 6. Incremental database write: only INSERT new works
    t0 = time.perf_counter()
    total_new = 0
    clean: list[dict] = []

    for star_id, name, new_items in sync_batches:
        star_cfg = next(s for s in stars if star_id_map[s.code] == star_id)
        sink = TitleSyncSink(star_id=star_id, star_code=star_cfg.code, star_name=name)
        new_codes = {it.code for it in new_items}
        batch_result = await sink.write_batch(new_items, new_codes, cover_map)

        total_new += batch_result["new"]
        log.info(f"done: {name}: {batch_result['new']} new")
        clean.append({"name": name, "titles": new_items, "count": batch_result["new"]})
    t1 = time.perf_counter()
    log.info(f"[timing] batch write all stars: {(t1 - t0) * 1000:.1f}ms")

    # Statistics
    t0 = time.perf_counter()
    rows = await db_write(_query_stats)
    t1 = time.perf_counter()
    log.info(f"[timing] stats query: {(t1 - t0) * 1000:.1f}ms")

    total = 0
    for _, name, count in rows:
        log.info(f"{name}: {count} titles")
        total += count
    log.info(f"sync complete: {total_new} new, {total} total titles | total elapsed={(time.perf_counter() - t_total) * 1000:.1f}ms")

    return clean


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
    semaphore: asyncio.Semaphore,
) -> dict:
    """Sync titles for a single star (background sync after adding a new actor)."""
    t0 = time.perf_counter()
    star_id = await db_write(
        db.upsert_star,
        name=star.name,
        handle=star.handle,
        code=star.code,
    )

    items = await fetch_star_page(fetcher, star, semaphore)
    if not items:
        return {"name": star.name, "count": 0, "titles": []}

    existing_codes = await db_write(db.load_all_title_codes)
    new_items = [it for it in items if (star_id, it.code) not in existing_codes]

    if len(new_items) > MAX_NEW_TITLES:
        new_items = new_items[:MAX_NEW_TITLES]

    if not new_items:
        return {"name": star.name, "count": 0, "titles": []}

    cover_items = [(it.code, it.cover_url or "") for it in new_items]
    cover_map = await download_covers_batch(cover_items, concurrency=8)

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
