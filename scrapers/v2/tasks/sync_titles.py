"""scrapers/v2/tasks/sync_titles.py — Sync actor latest titles

Diff-Sync architecture (first principle: update as fast as possible):
1. Batch upsert stars + preload all existing title codes (memory set)
2. Two-phase per-star pipeline:
   - Phase A (critical path, awaited by run()): ijavtorrent-only fetch →
     in-memory diff → incremental cover download → per-star UPSERT through
     the serial DB write queue. Stars overlap: one star's covers/writes hide
     under the remaining stars' fetch time.
   - Phase B (deferred, background task returned as outcome["rss_task"]):
     sukebei RSS supplement — merge into the ijav items kept in memory,
     same diff against the same snapshots, idempotent ON CONFLICT re-write
     with enriched magnets, rss-only codes appended. RSS failures never
     fail anything; they are logged and skipped.

Hybrid source: **ijavtorrent is the primary source** (actress pages carry the
rich metadata: retail dates, views, likes, cover_url, hhd800-tagged magnets).
**sukebei.nyaa.si RSS is the supplement/correction**: its per-star search
covers titles missing from ijavtorrent's listing (ijav lost most of its
catalog in 2026-08 and, even after recovery, shows sparse/capped actress
listings with no pagination, e.g. JULIA 61 cards vs 201 in DB) and adds
extra magnet candidates. Merge rule: ijavtorrent metadata wins, magnet
candidates are unioned (deduped by magnet), RSS-only codes are appended.

sukebei rate limiting (本地限流): all RSS requests go through one global
SukebeiRateLimiter — a leaky bucket with an adaptive interval. Sukebei
answers bursts with a soft block (HTTP 200, valid </rss>, 0 <item>s), so
the limiter backs off exponentially on 429s and on streaks of empty
responses, and decays back toward the floor on healthy ones.

Failure semantics: in phase A a star fails only when its ijavtorrent fetch
fails (stars without a star_page_url are RSS-only and never "fail" in
phase A). If every star fails phase A, run() raises so callers report a
sync error. Phase B has no failure semantics — per-star RSS errors are
warnings only.

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
from scrapers.v2.filters import drop_hidden

log = get_logger("sync-titles")

MAX_NEW_TITLES = 20  # Max new titles processed per sync (prevent stars with too many works from overwhelming sync)

MAX_FETCH_ATTEMPTS = 3  # Retries per star before declaring the fetch failed
FETCH_RETRY_DELAYS = (1.0, 2.0)  # Backoff between attempts (seconds); tests patch to zeros
RATE_LIMIT_RETRY_DELAY = 10.0  # Backoff after an HTTP 429 from sukebei
COVER_DOWNLOAD_CONCURRENCY = 16  # Parallel cover downloads; DMM CDN / ijav images tolerate this

# sukebei RSS adaptive rate limiting (tests monkeypatch these to 0)
RSS_BASE_INTERVAL = 1.0  # Floor: min seconds between any two sukebei requests (global)
RSS_MAX_INTERVAL = 30.0  # Backoff cap
RSS_SOFTBLOCK_THRESHOLD = 6  # Consecutive 0-item responses that mean a soft block

SUKEBEI_RSS_URL = "https://sukebei.nyaa.si/?page=rss&q={q}&c=0_0&f=0&s=id&o=desc"


class IncompletePageError(Exception):
    """Page fetched with HTTP 200 but looks truncated (stream cut or partial card list)."""


class NoRssResultsError(IncompletePageError):
    """All sukebei RSS queries for a star returned zero usable items.

    Distinct from a fetch failure: the source answered fine, the star just
    has no (matching) torrents. Phase B counts these into star_rss_state and
    suppresses repeat offenders instead of retrying them every sync.
    """


class SukebeiRateLimiter:
    """Global async leaky bucket shared by ALL sukebei requests in a run.

    Enforces a minimum interval between consecutive requests globally
    (lock + last-request timestamp + sleep-until-slot), replacing the old
    per-worker semaphore + fixed sleep, which could burst past 10 req/s and
    earned us soft blocks (HTTP 200, valid </rss>, 0 items).

    Adaptive interval: starts at RSS_BASE_INTERVAL; backoff() doubles it
    (capped at RSS_MAX_INTERVAL); each successful non-empty response decays
    it back toward the floor (×0.75). A streak of RSS_SOFTBLOCK_THRESHOLD
    consecutive 0-raw-item responses is treated as a soft block and backs
    off once (one obscure star returning 0 items is legit; six in a row is
    sukebei throttling us).
    """

    def __init__(self) -> None:
        self._interval = RSS_BASE_INTERVAL
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._zero_streak = 0

    @property
    def interval(self) -> float:
        return self._interval

    async def acquire(self) -> None:
        """Wait until the next global request slot opens."""
        async with self._lock:
            wait = self._last_request_at + self._interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    def backoff(self) -> None:
        """Double the interval (capped) — on HTTP 429 or a soft-block streak."""
        self._interval = min(self._interval * 2, RSS_MAX_INTERVAL)
        log.info(f"sukebei rate limiter backoff: interval now {self._interval:.1f}s")

    def note_response(self, raw_item_count: int) -> None:
        """Feed the raw <item> count of an RSS response (pre name-filtering)."""
        if raw_item_count > 0:
            self._zero_streak = 0
            self._interval = max(self._interval * 0.75, RSS_BASE_INTERVAL)
            return
        self._zero_streak += 1
        if self._zero_streak >= RSS_SOFTBLOCK_THRESHOLD:
            self.backoff()
            log.warning(
                f"sukebei soft-block suspected: {self._zero_streak} consecutive "
                f"0-item RSS responses; interval now {self._interval:.1f}s"
            )
            self._zero_streak = 0  # re-arm


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
    limiter: SukebeiRateLimiter,
) -> list[VideoItem]:
    """Fetch a single star's sukebei RSS searches and parse out VideoItems.

    All query variants (sync_query, name, jp) are fetched and their results
    merged by code — uploaders tag different spellings across torrents, so
    the first non-empty query is NOT good enough (e.g. romaji-only results
    would miss titles tagged with just the Japanese name).

    Every request goes through the shared rate limiter; the raw <item> count
    of each well-formed response feeds its soft-block detection.

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
                await limiter.acquire()
                rss = await fetcher.fetch(url)
            except Exception as e:
                last_err = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    rate_limited = True
                    limiter.backoff()
                    log.warning(f"rate limited 429 (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}")
                    break  # Long back-off before the next attempt, not the next query
                log.warning(f"rss fetch failed (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}: {type(e).__name__}: {e}")
                continue
            if not rss.rstrip().endswith("</rss>"):
                last_err = IncompletePageError("missing closing </rss> tag")
                log.warning(f"truncated rss (attempt {attempt}/{MAX_FETCH_ATTEMPTS}): {star.name} q={query!r}: no </rss>, len={len(rss)}")
                continue
            # Raw item count BEFORE name filtering: 0 raw items on a
            # well-formed page is sukebei's soft block, not a no-match.
            raw_items = rss.count("<item>")
            limiter.note_response(raw_items)
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
            if raw_items == 0:
                log.info(f"{star.name}: q={query!r}: 0 raw items (soft-block?)")
            elif not items:
                log.info(f"{star.name}: q={query!r}: {raw_items} raw items, 0 after name-filter (genuine no-match)")
            else:
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
        last_err = NoRssResultsError(f"0 usable items for all queries: {queries}")
    if isinstance(last_err, NoRssResultsError):
        # Not an outage — the star simply has no matching torrents. Phase B
        # negative-caches repeat offenders; keep the log at info.
        log.info(f"rss: {star.name}: 0 usable items for all queries: {queries}")
    else:
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


def _drop_hidden(
    items: list[VideoItem],
    star: StarConfig,
    roster_names: list[str],
) -> list[VideoItem]:
    """Filter out works that must not enter the library (see filters.py).

    IJavTorrentExtractor counts the /actress/ links on each card into
    star_count — solo works have exactly 1. RSS-supplement items have
    star_count=0 (unknown); the VR/keyword/cast-list/roster rules catch
    their VR and omnibus leaks instead.
    """
    kept, dropped = drop_hidden(items, [star.name, star.jp], roster_names)
    if dropped:
        codes = ", ".join(f"{c}({r})" for c, r in dropped[:10])
        more = f" +{len(dropped) - 10} more" if len(dropped) > 10 else ""
        log.info(f"{star.name}: filtered {len(dropped)} hidden titles: {codes}{more}")
    return kept


def _drop_blacklisted(
    items: list[VideoItem],
    star_id: int,
    blacklisted: set[tuple[int, str]],
) -> list[VideoItem]:
    """Drop titles on the blacklist (repeatedly rejected by the sink).

    Blacklisted codes never reach the diff, so no cover is downloaded for
    them — without this, sink-rejected titles would be re-fetched and
    re-rejected every sync, wasting a cover download each round.
    """
    kept = [it for it in items if (star_id, it.code) not in blacklisted]
    dropped = len(items) - len(kept)
    if dropped:
        log.info(f"star_id={star_id}: skipped {dropped} blacklisted titles")
    return kept


async def fetch_star(
    fetcher: HttpxFetcher,
    star: StarConfig,
    ijav_sem: asyncio.Semaphore,
    rss_limiter: SukebeiRateLimiter,
    roster_names: list[str] | None = None,
) -> list[VideoItem]:
    """Hybrid per-star fetch: ijavtorrent primary + sukebei RSS supplement.

    Used by the user-facing add-star flow (sync_star), which stays
    synchronous. run() uses the two-phase split instead (phase A ijav-only,
    phase B deferred RSS).

    One source failing degrades to the other with a loud warning; the star
    fails only when BOTH sources fail. VR and multi-star (共演/omnibus)
    titles are filtered out of the result.
    """
    roster_names = roster_names or []
    ijav_res, rss_res = await asyncio.gather(
        fetch_star_page(fetcher, star, ijav_sem),
        fetch_star_rss(fetcher, star, rss_limiter),
        return_exceptions=True,
    )
    ijav_err = ijav_res if isinstance(ijav_res, BaseException) else None
    rss_err = rss_res if isinstance(rss_res, BaseException) else None
    if ijav_err is not None and rss_err is not None:
        raise IncompletePageError(f"both sources failed: ijav={ijav_err}; rss={rss_err}")
    if ijav_err is not None:
        log.warning(f"{star.name}: ijavtorrent unavailable ({ijav_err}), sukebei-only degraded sync")
        return _drop_hidden(rss_res, star, roster_names)
    if rss_err is not None:
        log.warning(f"{star.name}: sukebei rss unavailable ({rss_err}), ijavtorrent-only degraded sync")
        return _drop_hidden(ijav_res, star, roster_names)
    return _drop_hidden(merge_sources(ijav_res, rss_res, star.name), star, roster_names)


def _split_sync_items(
    star_id: int,
    star_name: str,
    items: list[VideoItem],
    existing_codes: set,
    missing_codes: set,
    cover_missing: set,
) -> tuple[list[VideoItem], list[VideoItem], list[VideoItem], list[VideoItem]]:
    """Diff fetched items against the DB snapshots.

    Returns (new_items, backfill_items, cover_fix_items, sync_items).
    Titles whose cover is missing/unprocessed re-enter the pipeline when a
    source lists them — otherwise they would never get a cover retry (they
    have full metadata, so the metadata backfill never picks them up).
    """
    new_items = [it for it in items if (star_id, it.code) not in existing_codes]
    if len(new_items) > MAX_NEW_TITLES:
        log.info(f"{star_name}: {len(new_items)} new, limiting to {MAX_NEW_TITLES}")
        new_items = new_items[:MAX_NEW_TITLES]
    backfill_items = [
        it for it in items
        if (star_id, it.code) in missing_codes and (star_id, it.code) in existing_codes
    ]
    backfill_codes = {it.code for it in backfill_items}
    cover_fix_items = [
        it for it in items
        if (star_id, it.code) in cover_missing
        and (star_id, it.code) in existing_codes
        and it.code not in backfill_codes
    ]
    return new_items, backfill_items, cover_fix_items, new_items + backfill_items + cover_fix_items


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


# Module-level reference to the in-flight phase-B task (prevents GC; a second
# run() while one is active skips starting another).
_rss_task: asyncio.Task | None = None


async def run(
    config_path: str = "config.json",
    fetch_concurrency: int = 8,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Main entry: read config, sync all stars (two phases).

    Phase A (awaited): per-star ijavtorrent-only pipeline — fetch → diff →
    covers → write, overlapped across stars.

    Phase B (deferred): sukebei RSS enrichment spawned as a background task
    (``outcome["rss_task"]``, also held in the module-level ``_rss_task``).
    Callers must not await it on the critical path; the sync_runs row
    reflects phase A only.

    ``on_progress`` (optional) is awaited with a phase dict at each stage so
    callers can stream live progress (e.g. SSE ``sync.progress`` events):
    ``prepare`` → per-star ``fetch``/``covers``/``write`` (pipelined) →
    background per-star ``rss`` → final ``rss_done``.

    Returns {"results": per-star sync summaries, "failed": per-star fetch
    failures, "rss_task": phase-B task | None}. Raises RuntimeError when
    every star's phase-A fetch fails — a total outage must surface as a
    sync error, not as "0 new titles".
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
    roster_names = [n for s in stars for n in (s.name, s.jp) if n]
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
    cover_missing = await db_write(db.load_title_codes_missing_cover)
    blacklisted = await db_write(db.load_blacklisted_codes)
    t1 = time.perf_counter()
    log.info(f"[timing] load existing codes: {(t1 - t0) * 1000:.1f}ms | count={len(existing_codes)} | missing={len(missing_codes)}")

    # 3-5. Phase A: per-star ijav-only pipeline (fetch → diff → covers →
    # write), overlapped across stars. A star's covers download and its rows
    # write while later stars are still fetching, hiding most cover/write
    # time under fetch time. The DB write queue serializes all writes
    # anyway, so per-star writes don't change write-side safety.
    t0 = time.perf_counter()
    fetched = 0
    failed: list[dict[str, str]] = []
    clean: list[dict] = []
    total_new = 0
    total_updated = 0
    ijav_by_star: dict[str, list[VideoItem]] = {}
    phase_a_covers: dict[str, set[str]] = {}  # star.code → codes with covers fetched in phase A

    async with HttpxFetcher() as fetcher:
        ijav_sem = asyncio.Semaphore(fetch_concurrency)
        cover_sem = asyncio.Semaphore(COVER_DOWNLOAD_CONCURRENCY)

        async def _process_star(star: StarConfig) -> None:
            nonlocal fetched, total_new, total_updated

            # Phase A fetch: ijavtorrent only. Stars without a star_page_url
            # are RSS-only — not a failure; phase B covers them.
            ok, count, err = True, 0, None
            if not star.star_page_url:
                ijav_by_star[star.code] = []
                fetched += 1
                await _emit("fetch", star=star.name, done=fetched, total=len(stars),
                            ok=ok, titles=0)
                return
            try:
                items = _drop_hidden(
                    await fetch_star_page(fetcher, star, ijav_sem), star, roster_names
                )
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
            ijav_by_star[star.code] = items
            await _emit("fetch", star=star.name, done=fetched, total=len(stars),
                        ok=ok, titles=count)

            # Diff: new works + existing works with missing metadata/covers
            star_id = star_id_map[star.code]
            items = _drop_blacklisted(items, star_id, blacklisted)
            if not items:
                log.info(f"{star.name}: no new titles")
                return
            new_items, backfill_items, cover_fix_items, sync_items = _split_sync_items(
                star_id, star.name, items, existing_codes, missing_codes, cover_missing
            )
            if not sync_items:
                log.info(f"{star.name}: no new titles")
                return
            log.info(f"{star.name}: {len(new_items)} new, {len(backfill_items)} backfill, {len(cover_fix_items)} cover-fix")

            # Covers for this star (shared global cap + shared HTTP client),
            # then write — both overlap with other stars' fetches.
            # Only download covers we actually need: new titles, or existing
            # ones whose cover is missing. Backfill rows (metadata/magnet
            # refreshes) already have covers — re-downloading them every sync
            # wasted ~500 downloads and rewrote the blob column each run.
            try:
                cover_items = [
                    (it.code, it.cover_url or "") for it in sync_items
                    if (star_id, it.code) not in existing_codes
                    or (star_id, it.code) in cover_missing
                ]
                cover_map = await download_covers_batch(
                    cover_items, concurrency=COVER_DOWNLOAD_CONCURRENCY,
                    sem=cover_sem, fetcher=fetcher,
                )
                phase_a_covers[star.code] = set(cover_map.keys())
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

    # Phase B: deferred sukebei RSS enrichment (background; never blocks or
    # fails the sync). Skipped when a previous phase B is still running.
    global _rss_task
    rss_task: asyncio.Task | None = None
    if _rss_task is not None and not _rss_task.done():
        log.warning("previous rss enrichment still running; skipping phase B for this run")
    elif stars:
        _rss_task = asyncio.create_task(_rss_enrich_all(
            stars, ijav_by_star, star_id_map,
            existing_codes, missing_codes, cover_missing, blacklisted,
            roster_names, phase_a_covers, on_progress,
        ))
        rss_task = _rss_task

    return {
        "results": clean,
        "failed": failed,
        "total_new": total_new,
        "total_updated": total_updated,
        "rss_task": rss_task,
    }


async def _rss_enrich_all(
    stars: list[StarConfig],
    ijav_by_star: dict[str, list[VideoItem]],
    star_id_map: dict[str, int],
    existing_codes: set,
    missing_codes: set,
    cover_missing: set,
    blacklisted: set[tuple[int, str]],
    roster_names: list[str],
    phase_a_covers: dict[str, set[str]],
    on_progress: ProgressCallback | None,
) -> None:
    """Phase B: sukebei RSS supplement, deferred off the sync critical path.

    Per star (serialized by the shared rate limiter): fetch RSS → merge into
    the phase-A ijav items → same diff against the same snapshots → covers
    only for codes phase A did not cover → idempotent ON CONFLICT write with
    the magnet-enriched merged data. RSS failures are logged and skipped —
    they never fail anything.
    """
    async def _emit(phase: str, **kw: Any) -> None:
        if on_progress is not None:
            try:
                await on_progress({"phase": phase, **kw})
            except Exception:
                log.exception(f"progress callback failed (phase={phase})")

    limiter = SukebeiRateLimiter()
    n_enriched = 0
    n_new = 0
    n_failed = 0
    suppressed = await db_write(db.load_rss_suppressed_stars)
    if suppressed:
        log.info(f"phase B: {len(suppressed)} stars rss-suppressed (repeated empty results)")

    async with HttpxFetcher() as fetcher:
        cover_sem = asyncio.Semaphore(COVER_DOWNLOAD_CONCURRENCY)
        for i, star in enumerate(stars, 1):
            star_id = star_id_map[star.code]
            if star_id in suppressed:
                await _emit("rss", star=star.name, done=i, total=len(stars),
                            enriched=0, new=0, suppressed=True)
                continue
            try:
                rss_items = await fetch_star_rss(fetcher, star, limiter)
            except NoRssResultsError:
                await db_write(db.record_rss_empty, star_id)
                await _emit("rss", star=star.name, done=i, total=len(stars), enriched=0, new=0)
                continue
            except Exception as e:
                n_failed += 1
                log.warning(f"phase B: rss failed for {star.name}: {type(e).__name__}: {e}")
                await _emit("rss", star=star.name, done=i, total=len(stars),
                            enriched=0, new=0, error=f"{type(e).__name__}: {e}"[:200])
                continue
            # The source answered with usable items — reset any empty streak.
            await db_write(db.clear_rss_empty, star_id)

            merged = _drop_hidden(
                merge_sources(ijav_by_star.get(star.code, []), rss_items, star.name),
                star, roster_names,
            )
            merged = _drop_blacklisted(merged, star_id, blacklisted)
            if not merged:
                await _emit("rss", star=star.name, done=i, total=len(stars), enriched=0, new=0)
                continue
            new_items, backfill_items, cover_fix_items, sync_items = _split_sync_items(
                star_id, star.name, merged, existing_codes, missing_codes, cover_missing
            )
            enriched = len(backfill_items) + len(cover_fix_items)
            if not sync_items:
                await _emit("rss", star=star.name, done=i, total=len(stars), enriched=0, new=0)
                continue

            # Covers: only for codes phase A did not already fetch one for.
            already_covered = phase_a_covers.get(star.code, set())
            cover_items = [
                (it.code, it.cover_url or "") for it in sync_items
                if ((star_id, it.code) not in existing_codes
                    or (star_id, it.code) in cover_missing)
                and it.code not in already_covered
            ]
            try:
                cover_map = await download_covers_batch(
                    cover_items, concurrency=COVER_DOWNLOAD_CONCURRENCY,
                    sem=cover_sem, fetcher=fetcher,
                )
                sink = TitleSyncSink(star_id=star_id, star_code=star.code, star_name=star.name)
                await sink.write_batch(sync_items, {it.code for it in new_items}, cover_map)
            except Exception as e:
                n_failed += 1
                log.error(f"phase B: write failed for {star.name}: {e}")
                await _emit("rss", star=star.name, done=i, total=len(stars),
                            enriched=0, new=0, error=f"{type(e).__name__}: {e}"[:200])
                continue

            n_enriched += enriched
            n_new += len(new_items)
            log.info(f"phase B: {star.name}: {len(new_items)} new/rss-only, {enriched} enriched")
            await _emit("rss", star=star.name, done=i, total=len(stars),
                        enriched=enriched, new=len(new_items))

    log.info(
        f"rss enrichment done: {len(stars)} stars, {n_enriched} enriched, "
        f"{n_new} rss-only new, {n_failed} failed"
    )
    await _emit("rss_done", stars=len(stars), enriched=n_enriched,
                new=n_new, failed=n_failed)


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
    try:
        with open("config.json", encoding="utf-8") as f:
            roster_names = [
                n for s in json.load(f).get("stars", [])
                for n in (s.get("name"), s.get("jp")) if n
            ]
    except OSError:
        roster_names = []
    items = await fetch_star(
        fetcher, star, asyncio.Semaphore(1), SukebeiRateLimiter(), roster_names
    )
    blacklisted = await db_write(db.load_blacklisted_codes)
    items = _drop_blacklisted(items, star_id, blacklisted)
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

    async def _main() -> None:
        outcome = await run(config)
        # CLI runs must await the deferred RSS enrichment — asyncio.run
        # cancels pending tasks on loop close.
        rss_task = outcome.get("rss_task")
        if rss_task is not None:
            await rss_task

    asyncio.run(_main())
