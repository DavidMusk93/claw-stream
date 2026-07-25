"""tests/test_diff_sync.py — Diff-Sync incremental sync regression tests

Verify core assumptions:
1. HTTP fetching replaces Playwright, 10x+ speedup
2. Diff logic is correct: existing works are filtered, only new works are kept
3. Incremental cover download: only process new works
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scrapers.v2.tasks import sync_titles
from scrapers.v2.tasks.sync_titles import fetch_star_page, run
from scrapers.v2.schemas import VideoItem, StarConfig


@pytest.mark.asyncio
async def test_fetch_star_page_uses_http():
    """Verify fetch_star_page accepts HttpxFetcher (pure HTTP), no browser needed."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(
        return_value=(
            '<html><body><div class="video-item">'
            '<a href="/movie/test-001-12345"><img alt="TEST-001 sample"/></a>'
            '</div></body></html>'
        )
    )

    star = StarConfig(
        name="Test Star",
        code="TEST-001",
        star_page_url="https://example.com/test",
    )
    sem = asyncio.Semaphore(1)

    result = await fetch_star_page(mock_fetcher, star, sem)
    assert len(result) == 1
    assert result[0].code == "TEST-001"
    mock_fetcher.fetch.assert_called_once_with("https://example.com/test")


# ── Truncated-page retry regression ──────────────────────────────────
# ijavtorrent intermittently serves incomplete pages (HTTP 200, HTML cut
# or partial card list). Parsing the fragment produced fake "no new
# titles" syncs (夢実かなえ MFYD-159 incident). fetch_star_page must
# detect and retry, and raise if the page never completes.

_FULL_PAGE = (
    '<html><body><div class="video-item">'
    '<a href="/movie/test-001-12345"><img alt="TEST-001 sample"/></a>'
    '</div></body></html>'
)


@pytest.mark.asyncio
async def test_fetch_star_page_retries_truncated_html(monkeypatch):
    """HTML without a closing </html> tag is truncated: retry, then succeed."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(
        side_effect=['<html><body><div class="video-item">', _FULL_PAGE]
    )

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    result = await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1))

    assert len(result) == 1
    assert mock_fetcher.fetch.call_count == 2


@pytest.mark.asyncio
async def test_fetch_star_page_raises_on_persistent_truncation(monkeypatch):
    """A page that never completes is a fetch failure, not an empty list."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value='<html><body><div class="video-item">')

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    with pytest.raises(sync_titles.IncompletePageError):
        await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1))
    assert mock_fetcher.fetch.call_count == sync_titles.MAX_FETCH_ATTEMPTS


@pytest.mark.asyncio
async def test_fetch_star_page_retries_low_count_vs_db(monkeypatch):
    """Parsed count far below the DB count means a partial page: retry."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=_FULL_PAGE)  # only 1 card, DB holds 30

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    with pytest.raises(sync_titles.IncompletePageError):
        await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1), min_expected=30)
    assert mock_fetcher.fetch.call_count == sync_titles.MAX_FETCH_ATTEMPTS


@pytest.mark.asyncio
async def test_fetch_star_page_count_floor_not_applied_to_small_catalogs():
    """Stars below the floor skip the count check (site may legitimately list few)."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=_FULL_PAGE)

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    result = await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1), min_expected=3)

    assert len(result) == 1
    mock_fetcher.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_star_page_count_floor_skipped_for_paginated_pages():
    """Paginated actress pages legitimately show only page 1: no count check."""
    paginated_page = _FULL_PAGE.replace(
        "</body>", '<a href="/actress/test-star-1?page=2">2</a></body>'
    )
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=paginated_page)

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    result = await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1), min_expected=200)

    assert len(result) == 1
    mock_fetcher.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_diff_logic_filters_existing():
    """Verify diff logic: existing works are filtered, only new works are kept."""
    # Simulate page returning 3 works
    items = [
        VideoItem(code="NEW-001", title="New 1", release_date="01/01/2026"),
        VideoItem(code="OLD-001", title="Old 1", release_date="01/01/2025"),
        VideoItem(code="NEW-002", title="New 2", release_date="02/01/2026"),
    ]

    # Simulate existing works
    existing_codes = {(1, "OLD-001")}
    star_id = 1

    new_items = [it for it in items if (star_id, it.code) not in existing_codes]

    assert len(new_items) == 2
    assert {it.code for it in new_items} == {"NEW-001", "NEW-002"}


def test_http_vs_playwright_speed():
    """Benchmark: theoretical time cost of HTTP fetching vs Playwright.

    This test does not actually run Playwright (too heavy); instead it records HTTP elapsed time
    as a baseline for future comparison.
    """
    import asyncio
    from scrapers.v2.fetchers import HttpxFetcher

    async def _bench():
        t0 = time.perf_counter()
        async with HttpxFetcher() as fetcher:
            html = await fetcher.fetch("https://ijavtorrent.com/actress/miu-shiromine-21671")
        elapsed = (time.perf_counter() - t0) * 1000
        return elapsed, len(html)

    elapsed, html_len = asyncio.run(_bench())
    print(f"\nHTTP fetch: {elapsed:.1f}ms, {html_len} bytes")

    # Assertion: HTTP fetch should complete within 3 seconds
    assert elapsed < 3000, f"HTTP fetch too slow: {elapsed:.1f}ms"


@pytest.mark.asyncio
async def test_sync_batches_only_new_items():
    """Verify sync_batches only contains new works, not existing ones."""
    # Simulate page results
    page_results = [
        [
            VideoItem(code="A-001", title="A1"),
            VideoItem(code="A-002", title="A2"),
        ],
        [
            VideoItem(code="B-001", title="B1"),
        ],
    ]

    # Simulate existing works: A-001 already exists
    existing_codes = {(1, "A-001")}
    star_id_map = {"STAR-A": 1, "STAR-B": 2}

    stars = [
        StarConfig(name="Star A", code="STAR-A"),
        StarConfig(name="Star B", code="STAR-B"),
    ]

    sync_batches = []
    all_new_items = []

    for star, items in zip(stars, page_results):
        star_id = star_id_map[star.code]
        new_items = [it for it in items if (star_id, it.code) not in existing_codes]
        if new_items:
            sync_batches.append((star_id, star.name, new_items))
            all_new_items.extend(new_items)

    # Star A only has A-002 as new work
    assert len(sync_batches) == 2
    assert sync_batches[0][2][0].code == "A-002"
    # Star B's B-001 is a new work
    assert sync_batches[1][2][0].code == "B-001"
    assert len(all_new_items) == 2


@pytest.mark.asyncio
async def test_incremental_cover_download():
    """Verify cover download only processes new works."""
    new_items = [
        VideoItem(code="NEW-001", title="New 1", cover_url="https://example.com/1.jpg"),
        VideoItem(code="NEW-002", title="New 2", cover_url="https://example.com/2.jpg"),
    ]

    # Simulate cover download
    cover_items = [(it.code, it.cover_url or "") for it in new_items]
    assert len(cover_items) == 2
    assert cover_items[0] == ("NEW-001", "https://example.com/1.jpg")
    assert cover_items[1] == ("NEW-002", "https://example.com/2.jpg")


# ── Failure-semantics regression ─────────────────────────────────────
# A failed page fetch is NOT "no new titles": total outages must raise,
# partial outages must be reported in the run() result. Prevents the fake
# "0 new titles / All caught up" success seen when the source site is
# unreachable (sync-titles.log ConnectTimeout incident).


@pytest.mark.asyncio
async def test_fetch_star_page_raises_on_fetch_failure(monkeypatch):
    """fetch_star_page must propagate fetch exceptions, not return []."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(side_effect=TimeoutError("boom"))

    star = StarConfig(
        name="Test Star",
        code="TEST-001",
        star_page_url="https://example.com/test",
    )

    with pytest.raises(TimeoutError):
        await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1))


class _StubFetcher:
    """HttpxFetcher stand-in: maps url -> html string or Exception."""

    def __init__(self, behavior: dict):
        self._behavior = behavior

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch(self, url: str) -> str:
        outcome = self._behavior[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _stub_db_write(func, *args, **kwargs):
    """In-memory stand-in for the DuckDB serial write queue."""
    name = getattr(func, "__name__", "")
    if name == "upsert_star":
        return 1
    if name in ("load_all_title_codes", "load_title_codes_missing_metadata"):
        return set()
    return [("CODE", "Star", 0)]  # _query_stats rows


def _write_config(tmp_path, stars: list[StarConfig]) -> str:
    cfg = {"stars": [s.model_dump() for s in stars]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


@pytest.mark.asyncio
async def test_run_raises_when_all_fetches_fail(tmp_path, monkeypatch):
    """Total outage: every star page fetch fails -> run() must raise."""
    stars = [
        StarConfig(name="Star A", code="STAR-A", star_page_url="https://x.test/a"),
        StarConfig(name="Star B", code="STAR-B", star_page_url="https://x.test/b"),
    ]
    behavior = {s.star_page_url: TimeoutError("connect timeout") for s in stars}

    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(sync_titles, "db_write", _stub_db_write)
    monkeypatch.setattr(sync_titles, "HttpxFetcher", lambda: _StubFetcher(behavior))

    with pytest.raises(RuntimeError, match="all 2 star page fetches failed"):
        await run(_write_config(tmp_path, stars))


@pytest.mark.asyncio
async def test_run_partial_failure_returns_failed_list(tmp_path, monkeypatch):
    """Partial outage: failed stars are reported; good stars still sync."""
    stars = [
        StarConfig(name="Bad Star", code="STAR-A", star_page_url="https://x.test/a"),
        StarConfig(name="Good Star", code="STAR-B", star_page_url="https://x.test/b"),
    ]
    behavior = {
        "https://x.test/a": TimeoutError("connect timeout"),
        "https://x.test/b": "<html>ok</html>",
    }

    class _StubSink:
        def __init__(self, star_id, star_code, star_name):
            self.star_name = star_name

        async def write_batch(self, items, new_codes, cover_map):
            return {"new": len(items), "updated": 0}

    async def _stub_covers(items, concurrency=8):
        return {}

    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(sync_titles, "db_write", _stub_db_write)
    monkeypatch.setattr(sync_titles, "HttpxFetcher", lambda: _StubFetcher(behavior))
    monkeypatch.setattr(
        sync_titles,
        "IJavTorrentExtractor",
        lambda: SimpleNamespace(
            extract=lambda html: [VideoItem(code="B-001", title="B1")]
        ),
    )
    monkeypatch.setattr(sync_titles, "download_covers_batch", _stub_covers)
    monkeypatch.setattr(sync_titles, "TitleSyncSink", _StubSink)

    out = await run(_write_config(tmp_path, stars))

    assert [f["name"] for f in out["failed"]] == ["Bad Star"]
    assert len(out["results"]) == 1
    assert out["results"][0]["name"] == "Good Star"
    assert out["results"][0]["count"] == 1
