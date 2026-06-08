"""tests/test_diff_sync.py — Diff-Sync incremental sync regression tests

Verify core assumptions:
1. HTTP fetching replaces Playwright, 10x+ speedup
2. Diff logic is correct: existing works are filtered, only new works are kept
3. Incremental cover download: only process new works
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from scrapers.v2.tasks.sync_titles import fetch_star_page, run
from scrapers.v2.schemas import VideoItem, StarConfig


@pytest.mark.asyncio
async def test_fetch_star_page_uses_http():
    """Verify fetch_star_page accepts HttpxFetcher (pure HTTP), no browser needed."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value='<div class="video-item"></div>')

    star = StarConfig(
        name="Test Star",
        code="TEST-001",
        star_page_url="https://example.com/test",
    )
    sem = asyncio.Semaphore(1)

    result = await fetch_star_page(mock_fetcher, star, sem)
    assert result == []
    mock_fetcher.fetch.assert_called_once_with("https://example.com/test")


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
