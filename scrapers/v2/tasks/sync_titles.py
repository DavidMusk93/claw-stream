"""scrapers/v2/tasks/sync_titles.py — Sync actor latest titles

Diff-Sync architecture (first principle: update as fast as possible):
1. Batch upsert stars + preload all existing title codes (memory set)
2. Pure HTTP concurrent fetching of star pages (HttpxFetcher, no browser overhead)
3. In-memory diff: keep only new works
4. Incremental cover download: only download covers for new works
5. Incremental database write: only INSERT new works

Failure semantics: a failed page fetch is NOT "no new titles". Fetch errors
are collected per star; if every star page fails (e.g. source site
unreachable), run() raises so callers report a sync error instead of a fake
"0 new titles" success.

Truncated-page semantics: the source site intermittently returns incomplete
pages (HTTP 200 but the HTML stream is cut, or far fewer cards than the
star actually has). Parsing such a fragment yields a fake "no new titles"
diff. fetch_star_page() therefore validates page integrity (closing
</html> tag, parsed-count floor vs. DB count) and retries before giving up;
a persistently incomplete page is a fetch failure, not an empty title list.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from typing import Any

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

MAX_FETCH_ATTEMPTS = 3  # Retries per star page before declaring the fetch failed
FETCH_RETRY_DELAYS = (1.0, 2.0)  # Backoff between attempts (seconds); tests patch to zeros
MIN_COUNT_CHECK_FLOOR = 4  # Only apply the parsed-count floor when DB count >= this


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


async def fetch_star_page(
    fetcher: HttpxFetcher,
    star: StarConfig,
    semaphore: asyncio.Semaphore,
    min_expected: int = 0,
) -> list[VideoItem]:
    """Fetch a single star's page and parse out a list of VideoItems.

    Retries up to MAX_FETCH_ATTEMPTS times when the page looks truncated:
    the HTML stream lacks a closing </html> tag, or the parsed card count
    falls below half of what the DB already holds for this star (the source
    site intermittently serves partial pages with HTTP 200). The count
    floor only applies to single-page listings — paginated actress pages
    legitimately show only the first page's worth of cards.

    Raises on persistent failure: an unreachable or truncated page is an
    error, not an empty title list. Callers must handle the exception
    explicitly.
    """
    last_err: Exception | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            async with semaphore:
                html = await fetcher.fetch(star.star_page_url)
        except Exception as e:
            last_err = e
            log.warning(f"fetch failed (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name}: {type(e).__name__}: {e}")
        else:
            if not html.rstrip().endswith("</html>"):
                last_err = IncompletePageError("missing closing </html> tag")
                log.warning(f"truncated page (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name}: no </html>, len={len(html)}")
            else:
                extractor = IJavTorrentExtractor()
                raw_items = extractor.extract(html)
                items = _dedup(raw_items)
                log.info(f"{star.name}: {len(raw_items)} raw → {len(items)} dedup")
                paginated = re.search(r'href="[^"]*\?page=2"', html) is not None
                if not paginated and min_expected >= MIN_COUNT_CHECK_FLOOR and len(items) < min_expected / 2:
                    last_err = IncompletePageError(
                        f"parsed {len(items)} titles, DB holds {min_expected} (partial page?)"
                    )
                    log.warning(f"truncated page (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name}: {last_err}")
                else:
                    if not items:
                        log.warning(f"{star.name}: page fetched but 0 titles parsed (layout change or empty page?)")
                    return items
        if attempt < MAX_FETCH_ATTEMPTS:
            await asyncio.sleep(FETCH_RETRY_DELAYS[attempt - 1])
    log.error(f"fetch failed: {star.name}: {type(last_err).__name__}: {last_err}")
    raise last_err


async def run(config_path: str = "config.json", fetch_concurrency: int = 8) -> dict[str, Any]:
    """Main entry: read config, concurrently sync all stars.

    Returns {"results": per-star sync summaries, "failed": per-star fetch
    failures}. Raises RuntimeError when every star page fetch fails — a total
    outage must surface as a sync error, not as "0 new titles".
    """
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

    # 2. Preload existing title codes and missing-metadata codes into memory sets
    t0 = time.perf_counter()
    existing_codes = await db_write(db.load_all_title_codes)
    missing_codes = await db_write(db.load_title_codes_missing_metadata)
    t1 = time.perf_counter()
    log.info(f"[timing] load existing codes: {(t1 - t0) * 1000:.1f}ms | count={len(existing_codes)} | missing={len(missing_codes)}")

    # 3. Concurrently fetch all star pages (pure HTTP, no browser overhead)
    t0 = time.perf_counter()
    # Per-star DB title counts: floor for the truncated-page check. A page
    # parsing to less than half of the DB count is a partial page, retry it.
    expected_counts = Counter(star_id for star_id, _ in existing_codes)
    async with HttpxFetcher() as fetcher:
        sem = asyncio.Semaphore(fetch_concurrency)
        page_results = await asyncio.gather(
            *[
                fetch_star_page(
                    fetcher, star, sem,
                    min_expected=expected_counts.get(star_id_map[star.code], 0),
                )
                for star in stars
            ],
            return_exceptions=True,
        )
    t1 = time.perf_counter()
    log.info(f"[timing] fetch star pages: {(t1 - t0) * 1000:.1f}ms")

    # 4. Diff: new works + existing works with missing metadata
    sync_batches: list[tuple[int, str, list[VideoItem]]] = []
    all_sync_items: list[VideoItem] = []
    failed: list[dict[str, str]] = []

    for star, page_result in zip(stars, page_results):
        if isinstance(page_result, Exception):
            log.error(f"page fetch exception for {star.name}: {page_result}")
            failed.append({
                "name": star.name,
                "error": f"{type(page_result).__name__}: {page_result}",
            })
            continue

        star_id = star_id_map[star.code]
        items = page_result

        new_items = [it for it in items if (star_id, it.code) not in existing_codes]
        if len(new_items) > MAX_NEW_TITLES:
            log.info(f"{star.name}: {len(new_items)} new, limiting to {MAX_NEW_TITLES}")
            new_items = new_items[:MAX_NEW_TITLES]

        backfill_items = [
            it for it in items
            if (star_id, it.code) in missing_codes and (star_id, it.code) in existing_codes
        ]

        sync_items = new_items + backfill_items
        if sync_items:
            sync_batches.append((star_id, star.name, sync_items, new_items))
            all_sync_items.extend(sync_items)
            log.info(f"{star.name}: {len(new_items)} new, {len(backfill_items)} backfill")
        else:
            log.info(f"{star.name}: no new titles")

    # A total fetch failure means nothing was synced at all — surface it as an
    # error instead of reporting a fake "0 new titles" success.
    if stars and len(failed) == len(stars):
        raise RuntimeError(
            f"all {len(stars)} star page fetches failed: {failed[0]['error']}"
        )
    if failed:
        log.warning(f"{len(failed)}/{len(stars)} star pages failed to fetch: "
                    f"{', '.join(f['name'] for f in failed)}")

    # 5. Incremental cover download: only download covers for works we will write
    cover_map: dict[str, str] = {}
    if all_sync_items:
        t0 = time.perf_counter()
        cover_items = [(it.code, it.cover_url or "") for it in all_sync_items]
        log.info(f"downloading {len(cover_items)} covers in batch...")
        cover_map = await download_covers_batch(cover_items, concurrency=8)
        t1 = time.perf_counter()
        log.info(f"[timing] download covers: {(t1 - t0) * 1000:.1f}ms | downloaded={len(cover_map)}")

    # 6. Incremental database write: INSERT new + UPDATE missing metadata
    t0 = time.perf_counter()
    total_new = 0
    total_updated = 0
    clean: list[dict] = []

    for star_id, name, sync_items, new_items in sync_batches:
        star_cfg = next(s for s in stars if star_id_map[s.code] == star_id)
        sink = TitleSyncSink(star_id=star_id, star_code=star_cfg.code, star_name=name)
        new_codes = {it.code for it in new_items}
        batch_result = await sink.write_batch(sync_items, new_codes, cover_map)

        total_new += batch_result["new"]
        total_updated += batch_result["updated"]
        log.info(f"done: {name}: {batch_result['new']} new, {batch_result['updated']} backfill")
        clean.append({"name": name, "titles": sync_items, "count": batch_result["new"]})
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

    existing_codes = await db_write(db.load_all_title_codes)
    min_expected = sum(1 for sid, _ in existing_codes if sid == star_id)
    items = await fetch_star_page(fetcher, star, semaphore, min_expected=min_expected)
    if not items:
        return {"name": star.name, "count": 0, "titles": []}

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
