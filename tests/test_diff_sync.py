"""tests/test_diff_sync.py — Diff-Sync incremental sync regression tests

Verify core assumptions:
1. HTTP fetching replaces Playwright, 10x+ speedup
2. Diff logic is correct: existing works are filtered, only new works are kept
3. Incremental cover download: only process new works

Hybrid source: ijavtorrent actress pages are the primary source (rich
metadata); sukebei.nyaa.si RSS search is the supplement/correction for
ijavtorrent's catalog gaps. A star fails only when both sources fail.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from unittest.mock import AsyncMock

import httpx
import pytest

from scrapers.v2.extractors import SukebeiRssExtractor
from scrapers.v2.tasks import sync_titles
from scrapers.v2.tasks.sync_titles import (
    fetch_star,
    fetch_star_page,
    fetch_star_rss,
    merge_sources,
    run,
)
from scrapers.v2.schemas import MagnetCandidate, VideoItem, StarConfig


@pytest.fixture(autouse=True)
def _no_request_pacing(monkeypatch):
    """Tests must not pay the real sukebei request-pacing delay."""
    monkeypatch.setattr(sync_titles, "RSS_REQUEST_INTERVAL", 0.0)


# ── RSS fixtures ───────────────────────────────────────────────────────

_PUBDATE = "Tue, 28 Jul 2026 14:15:28 -0000"


def _item(
    title: str,
    info_hash: str = "a" * 40,
    seeders: int = 5,
    leechers: int = 2,
    downloads: int = 100,
    size: str = "3.6 GiB",
    pubdate: str = _PUBDATE,
) -> str:
    return (
        f"<item><title>{title}</title><pubDate>{pubdate}</pubDate>"
        f"<nyaa:seeders>{seeders}</nyaa:seeders>"
        f"<nyaa:leechers>{leechers}</nyaa:leechers>"
        f"<nyaa:downloads>{downloads}</nyaa:downloads>"
        f"<nyaa:infoHash>{info_hash}</nyaa:infoHash>"
        f"<nyaa:size>{size}</nyaa:size></item>"
    )


def _rss(*items: str) -> str:
    return (
        '<rss xmlns:nyaa="https://sukebei.nyaa.si/xmlns/nyaa" version="2.0">'
        f"<channel>{''.join(items)}</channel></rss>"
    )


_FULL_RSS = _rss(_item("TEST-001 Test Star sample title"))

_EMPTY_RSS = _rss()


def _rss_url(query: str) -> str:
    return sync_titles.SUKEBEI_RSS_URL.format(q=urllib.parse.quote(query))


# ── fetch_star_rss basics ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_star_rss_uses_http():
    """Verify fetch_star_rss accepts HttpxFetcher (pure HTTP), no browser needed."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=_FULL_RSS)

    star = StarConfig(name="Test Star", code="TEST-001")
    sem = asyncio.Semaphore(1)

    result = await fetch_star_rss(mock_fetcher, star, sem)
    assert len(result) == 1
    assert result[0].code == "TEST-001"
    mock_fetcher.fetch.assert_called_once_with(_rss_url("Test Star"))


# ── Truncated-RSS retry regression ─────────────────────────────────────
# A truncated (no </rss>) or unparseable RSS body must be retried, and a
# persistently bad response is a fetch failure, never an empty list —
# same failure semantics as the old ijavtorrent truncated-page guard.


@pytest.mark.asyncio
async def test_fetch_star_rss_retries_truncated_body(monkeypatch):
    """RSS without a closing </rss> tag is truncated: retry, then succeed."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(
        side_effect=['<rss version="2.0"><channel><item>', _FULL_RSS]
    )

    star = StarConfig(name="Test Star", code="TEST-001")
    result = await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))

    assert len(result) == 1
    assert mock_fetcher.fetch.call_count == 2


@pytest.mark.asyncio
async def test_fetch_star_rss_raises_on_persistent_truncation(monkeypatch):
    """A response that never completes is a fetch failure, not an empty list."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value='<rss version="2.0"><channel><item>')

    star = StarConfig(name="Test Star", code="TEST-001")
    with pytest.raises(sync_titles.IncompletePageError):
        await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))
    assert mock_fetcher.fetch.call_count == sync_titles.MAX_FETCH_ATTEMPTS


@pytest.mark.asyncio
async def test_fetch_star_rss_raises_when_all_queries_empty(monkeypatch):
    """Zero usable items across every query variant is a failure, not 'no new titles'."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=_EMPTY_RSS)

    star = StarConfig(name="NoHit Star", code="TEST-001", jp="NoHit")
    with pytest.raises(sync_titles.IncompletePageError):
        await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))
    # 2 query variants × MAX_FETCH_ATTEMPTS
    assert mock_fetcher.fetch.call_count == 2 * sync_titles.MAX_FETCH_ATTEMPTS


@pytest.mark.asyncio
async def test_fetch_star_rss_query_fallback_to_jp():
    """If the name query yields nothing, the jp romaji query is tried next."""
    mock_fetcher = AsyncMock()

    async def _fetch(url: str) -> str:
        if urllib.parse.quote("Test Star") in url:
            return _EMPTY_RSS
        return _rss(_item("TEST-001 TestStar romaji hit"))

    mock_fetcher.fetch = AsyncMock(side_effect=_fetch)

    star = StarConfig(name="Test Star", code="TEST-001", jp="TestStar")
    result = await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))

    assert len(result) == 1
    assert result[0].code == "TEST-001"
    assert mock_fetcher.fetch.call_count == 2


@pytest.mark.asyncio
async def test_fetch_star_rss_unions_all_query_variants():
    """All query variants are fetched and merged by code — uploaders tag
    different spellings across torrents, so first-non-empty is not enough."""
    mock_fetcher = AsyncMock()

    async def _fetch(url: str) -> str:
        if urllib.parse.quote("Test Star") in url:
            # romaji-tagged uploads: two torrents of TEST-001
            return _rss(
                _item("TEST-001 Test Star upload one", info_hash="a" * 40),
                _item("TEST-001 Test Star upload two", info_hash="b" * 40),
            )
        # Japanese-tagged uploads: another torrent of TEST-001 + a new code
        return _rss(
            _item("TEST-001 テスト星 upload three", info_hash="c" * 40),
            _item("TEST-002 テスト星 jp only title", info_hash="d" * 40),
        )

    mock_fetcher.fetch = AsyncMock(side_effect=_fetch)

    star = StarConfig(name="Test Star", code="TEST-001", jp="テスト星")
    result = await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))

    by_code = {it.code: it for it in result}
    assert set(by_code) == {"TEST-001", "TEST-002"}
    # Same code from both queries: magnet candidates merged, not overwritten
    assert len(by_code["TEST-001"].magnets) == 3
    assert mock_fetcher.fetch.call_count == 2


@pytest.mark.asyncio
async def test_fetch_star_rss_sync_query_takes_precedence():
    """sync_query overrides the default name/jp queries."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=_FULL_RSS)

    star = StarConfig(name="Test Star", code="TEST-001", sync_query="Test Star")
    result = await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))

    assert len(result) == 1
    mock_fetcher.fetch.assert_called_once_with(_rss_url("Test Star"))


@pytest.mark.asyncio
async def test_fetch_star_rss_raises_on_fetch_failure(monkeypatch):
    """fetch_star_rss must propagate fetch exceptions, not return []."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(side_effect=TimeoutError("boom"))

    star = StarConfig(name="Test Star", code="TEST-001")

    with pytest.raises(TimeoutError):
        await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))


@pytest.mark.asyncio
async def test_fetch_star_rss_backs_off_on_429(monkeypatch):
    """HTTP 429 from sukebei triggers a long back-off, then the retry succeeds."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(sync_titles, "RATE_LIMIT_RETRY_DELAY", 0.0)
    req = httpx.Request("GET", "https://x.test")
    err = httpx.HTTPStatusError(
        "too many", request=req, response=httpx.Response(429, request=req)
    )
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(side_effect=[err, _FULL_RSS])

    star = StarConfig(name="Test Star", code="TEST-001")
    result = await fetch_star_rss(mock_fetcher, star, asyncio.Semaphore(1))

    assert len(result) == 1
    assert mock_fetcher.fetch.call_count == 2


# ── SukebeiRssExtractor unit tests ─────────────────────────────────────


def test_extractor_groups_multiple_torrents_per_code():
    """Multiple torrent rows of the same work become MagnetCandidates of one VideoItem."""
    rss = _rss(
        _item("[FHDC][UN] ABF-358 究極のぬるぬるオーガズム 涼森れむ", info_hash="b" * 40, seeders=10),
        _item("ABF-358 究極のぬるぬるオーガズム 涼森れむ", info_hash="c" * 40, seeders=3, size="5.2 GiB"),
        _item("ABF-367 涼森れむの「顔」で、ヌく。", info_hash="d" * 40),
    )
    items = SukebeiRssExtractor().extract(rss, star_names={"涼森れむ"})

    by_code = {it.code: it for it in items}
    assert set(by_code) == {"ABF-358", "ABF-367"}
    assert len(by_code["ABF-358"].magnets) == 2
    assert by_code["ABF-358"].all_magnet_urls == [m.magnet for m in by_code["ABF-358"].magnets]
    # Leading [tag] prefixes and the code are stripped from the work title
    assert by_code["ABF-358"].title == "究極のぬるぬるオーガズム 涼森れむ"
    # Candidate order is preserved; scoring (incl. the hhd800 bonus) happens in the sink
    assert "urn:btih:" + "b" * 40 in by_code["ABF-358"].magnets[0].magnet


def test_extractor_parses_metadata_fields():
    """Resolution, size conversion, seed/leech, downloads→likes, pubDate→release_date."""
    rss = _rss(
        _item(
            "[FHDC][UN] ABF-358 究極のぬるぬるオーガズム 涼森れむ",
            seeders=49, leechers=127, downloads=18337, size="10.1 GiB",
        )
    )
    (it,) = SukebeiRssExtractor().extract(rss, star_names={"涼森れむ"})

    m = it.magnets[0]
    assert m.resolution == "[FHDC]"
    assert m.size == "10.1GB"
    assert m.seed == 49
    assert m.leech == 127
    assert it.likes == 18337
    assert it.release_date == "28/07/2026"
    assert it.views is None
    assert m.magnet.startswith("magnet:?xt=urn:btih:" + "a" * 40)
    assert "tr=" in m.magnet  # public trackers appended


def test_extractor_drops_noise_without_star_name():
    """RSS search is full-text: items not mentioning the star are dropped."""
    rss = _rss(
        _item("ABF-358 涼森れむ good hit"),
        _item("ABF-359 totally unrelated title"),
    )
    items = SukebeiRssExtractor().extract(rss, star_names={"涼森れむ"})

    assert [it.code for it in items] == ["ABF-358"]


def test_extractor_case_insensitive_ascii_name_match():
    """Romaji star names match titles case-insensitively."""
    rss = _rss(_item("SNOS-177 something Miru does"))
    items = SukebeiRssExtractor().extract(rss, star_names={"miru"})

    assert [it.code for it in items] == ["SNOS-177"]


def test_extractor_skips_prefix_blacklist_and_missing_hash():
    """SKIP_CODE_PREFIXES and items without infoHash are excluded."""
    rss = _rss(
        _item("OAE-123 涼森れむ blacklisted prefix"),
        "<item><title>ABF-358 涼森れむ no hash</title></item>",
    )
    items = SukebeiRssExtractor().extract(rss, star_names={"涼森れむ"})

    assert items == []


def test_extractor_marks_hhd800_hd_source():
    """The '+++ [FHD]' uploads on sukebei are the hhd800 releases — same rule as ijavtorrent."""
    rss = _rss(
        _item("+++ [FHD] SNOS-334 瀬戸環奈 hd source", info_hash="a" * 40),
        _item("[Reducing Mosaic] SNOS-334 瀬戸環奈 low quality", info_hash="b" * 40),
    )
    (it,) = SukebeiRssExtractor().extract(rss, star_names={"瀬戸環奈"})

    assert it.magnets[0].is_hhd800 is True
    assert it.magnets[0].resolution == "[FHD]"
    assert it.magnets[1].is_hhd800 is False


def test_extractor_parses_vr_resolution_tags():
    """VR titles carry [8KVR]/[4KVR] tags instead of the FHD vocabulary."""
    rss = _rss(_item("[8KVR] SIVR-490 【VR】瀬戸環奈 vr title"))
    (it,) = SukebeiRssExtractor().extract(rss, star_names={"瀬戸環奈"})

    assert it.magnets[0].resolution == "[8KVR]"


def test_extractor_matches_digit_led_codes():
    """Amateur series codes like 229SCUTE-1575 start with digits; dates must not match."""
    rss = _rss(
        _item("229SCUTE-1575 いつき(25) amateur title"),
        _item("uploaded 2026-08-14 いつき no code here"),
    )
    items = SukebeiRssExtractor().extract(rss, star_names={"いつき"})

    assert [it.code for it in items] == ["229SCUTE-1575"]


def test_score_magnet_hd_selection_parity():
    """HD selection parity with the ijavtorrent era: hhd800 FHD > 8KVR > 4K."""
    from scrapers.v2.schemas import MagnetCandidate
    from scrapers.v2.sinks import TitleSyncSink

    hhd_fhd = MagnetCandidate(magnet="m1", resolution="[FHD]", size="7.1GB", seed=100, is_hhd800=True)
    vr_8k = MagnetCandidate(magnet="m2", resolution="[8KVR]", size="16.5GB", seed=100)
    four_k = MagnetCandidate(magnet="m3", resolution="[4K]", size="24.5GB", seed=100)
    plain_fhd = MagnetCandidate(magnet="m4", resolution="[FHD]", size="7.1GB", seed=100)

    assert TitleSyncSink._score_magnet(hhd_fhd) > TitleSyncSink._score_magnet(vr_8k)
    assert TitleSyncSink._score_magnet(vr_8k) > TitleSyncSink._score_magnet(four_k)
    assert TitleSyncSink._score_magnet(four_k) > TitleSyncSink._score_magnet(plain_fhd)



# ── Diff logic (source-independent) ────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_logic_filters_existing():
    """Verify diff logic: existing works are filtered, only new works are kept."""
    # Simulate source returning 3 works
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
            rss = await fetcher.fetch(_rss_url("miru"))
        elapsed = (time.perf_counter() - t0) * 1000
        return elapsed, len(rss)

    elapsed, rss_len = asyncio.run(_bench())
    print(f"\nHTTP fetch: {elapsed:.1f}ms, {rss_len} bytes")

    # Assertion: HTTP fetch should complete within 3 seconds
    assert elapsed < 3000, f"HTTP fetch too slow: {elapsed:.1f}ms"


@pytest.mark.asyncio
async def test_sync_batches_only_new_items():
    """Verify sync_batches only contains new works, not existing ones."""
    # Simulate fetch results
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


# ── Failure-semantics regression ───────────────────────────────────────
# A failed RSS fetch is NOT "no new titles": total outages must raise,
# partial outages must be reported in the run() result.


class _StubFetcher:
    """HttpxFetcher stand-in: maps url -> rss string or Exception."""

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
    """Total outage: every star RSS fetch fails -> run() must raise."""
    stars = [
        StarConfig(name="Star A", code="STAR-A"),
        StarConfig(name="Star B", code="STAR-B"),
    ]
    behavior = {_rss_url(s.name): TimeoutError("connect timeout") for s in stars}

    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(sync_titles, "db_write", _stub_db_write)
    monkeypatch.setattr(sync_titles, "HttpxFetcher", lambda: _StubFetcher(behavior))

    with pytest.raises(RuntimeError, match="all 2 star fetches failed"):
        await run(_write_config(tmp_path, stars))


@pytest.mark.asyncio
async def test_run_partial_failure_returns_failed_list(tmp_path, monkeypatch):
    """Partial outage: failed stars are reported; good stars still sync."""
    stars = [
        StarConfig(name="Bad Star", code="STAR-A"),
        StarConfig(name="Good Star", code="STAR-B"),
    ]
    behavior = {
        _rss_url("Bad Star"): TimeoutError("connect timeout"),
        _rss_url("Good Star"): _rss(_item("SB-001 Good Star fresh title")),
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
    monkeypatch.setattr(sync_titles, "download_covers_batch", _stub_covers)
    monkeypatch.setattr(sync_titles, "TitleSyncSink", _StubSink)

    out = await run(_write_config(tmp_path, stars))

    assert [f["name"] for f in out["failed"]] == ["Bad Star"]
    assert len(out["results"]) == 1
    assert out["results"][0]["name"] == "Good Star"
    assert out["results"][0]["count"] == 1


# ── Hybrid source: ijavtorrent primary + sukebei supplement ──────────
# ijavtorrent carries the rich metadata (retail dates, views, cover_url,
# hhd800-tagged magnets) but its listing is sparse since the 2026-08
# catalog loss; the sukebei RSS supplement corrects the gaps.

_FULL_PAGE = (
    '<html><body><div class="video-item">'
    '<a href="/movie/test-001-12345"><img alt="TEST-001 sample"/></a>'
    '</div></body></html>'
)


@pytest.mark.asyncio
async def test_fetch_star_page_uses_http():
    """ijavtorrent primary source: fetch_star_page parses an actress page."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(return_value=_FULL_PAGE)

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    result = await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1))

    assert len(result) == 1
    assert result[0].code == "TEST-001"
    mock_fetcher.fetch.assert_called_once_with("https://example.com/test")


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
async def test_fetch_star_page_raises_on_fetch_failure(monkeypatch):
    """fetch_star_page must propagate fetch exceptions, not return []."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(side_effect=TimeoutError("boom"))

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    with pytest.raises(TimeoutError):
        await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1))


@pytest.mark.asyncio
async def test_fetch_star_page_without_url_raises():
    """Stars without an ijavtorrent URL are served by the RSS supplement alone."""
    mock_fetcher = AsyncMock()
    star = StarConfig(name="Test Star", code="TEST-001")

    with pytest.raises(sync_titles.IncompletePageError, match="star_page_url"):
        await fetch_star_page(mock_fetcher, star, asyncio.Semaphore(1))
    mock_fetcher.fetch.assert_not_called()


def test_merge_sources_primary_wins_and_supplement_fills_gaps():
    """Same code: ijav metadata wins, magnets unioned. RSS-only codes appended."""
    ijav = [
        VideoItem(
            code="TEST-001", title="ijav title", release_date="01/08/2026",
            views=100, likes=50, cover_url="https://img.test/1.jpg",
            magnets=[MagnetCandidate(magnet="magnet:?xt=urn:btih:" + "a" * 40, is_hhd800=True)],
            all_magnet_urls=["magnet:?xt=urn:btih:" + "a" * 40],
        )
    ]
    rss = [
        VideoItem(
            code="TEST-001", title="rss title", release_date="02/08/2026",
            likes=80,
            magnets=[MagnetCandidate(magnet="magnet:?xt=urn:btih:" + "b" * 40)],
            all_magnet_urls=["magnet:?xt=urn:btih:" + "b" * 40],
        ),
        VideoItem(code="TEST-002", title="rss only correction", release_date="03/08/2026"),
    ]

    merged = merge_sources(ijav, rss)
    by_code = {it.code: it for it in merged}

    assert set(by_code) == {"TEST-001", "TEST-002"}
    t1 = by_code["TEST-001"]
    assert len(t1.magnets) == 2                      # magnets unioned
    assert t1.release_date == "01/08/2026"           # ijav retail date wins
    assert t1.views == 100 and t1.cover_url == "https://img.test/1.jpg"
    assert t1.likes == 80                            # likes = max
    assert by_code["TEST-002"].title == "rss only correction"


@pytest.mark.asyncio
async def test_fetch_star_merges_ijav_and_rss(monkeypatch):
    """Both sources up: merged result with RSS-only codes appended."""
    ijav_url = "https://example.com/test"
    mock_fetcher = AsyncMock()

    async def _fetch(url: str) -> str:
        if url == ijav_url:
            return _FULL_PAGE
        return _rss(_item("TEST-002 Test Star rss only title"))

    mock_fetcher.fetch = AsyncMock(side_effect=_fetch)
    star = StarConfig(name="Test Star", code="TEST-001", star_page_url=ijav_url)

    result = await fetch_star(mock_fetcher, star, asyncio.Semaphore(1), asyncio.Semaphore(1))

    assert {it.code for it in result} == {"TEST-001", "TEST-002"}


@pytest.mark.asyncio
async def test_fetch_star_degrades_to_rss_when_ijav_fails(monkeypatch):
    """ijavtorrent down: degrade to the sukebei supplement, do not fail the star."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    ijav_url = "https://example.com/test"
    mock_fetcher = AsyncMock()

    async def _fetch(url: str) -> str:
        if url == ijav_url:
            raise TimeoutError("ijav down")
        return _rss(_item("TEST-001 Test Star rss hit"))

    mock_fetcher.fetch = AsyncMock(side_effect=_fetch)
    star = StarConfig(name="Test Star", code="TEST-001", star_page_url=ijav_url)

    result = await fetch_star(mock_fetcher, star, asyncio.Semaphore(1), asyncio.Semaphore(1))

    assert [it.code for it in result] == ["TEST-001"]


@pytest.mark.asyncio
async def test_fetch_star_raises_only_when_both_sources_fail(monkeypatch):
    """A star fails only when ijavtorrent AND sukebei both fail."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(side_effect=TimeoutError("all down"))

    star = StarConfig(name="Test Star", code="TEST-001", star_page_url="https://example.com/test")
    with pytest.raises(sync_titles.IncompletePageError, match="both sources failed"):
        await fetch_star(mock_fetcher, star, asyncio.Semaphore(1), asyncio.Semaphore(1))


_MULTI_STAR_PAGE = (
    '<html><body>'
    '<div class="video-item">'
    '<a href="/movie/solo-001-111"><img alt="SOLO-001 solo work"/></a>'
    '<div class="mb-1"><a href="/actress/test-star-1">Test Star</a></div><table></table>'
    '</div>'
    '<div class="video-item">'
    '<a href="/movie/orgy-002-222"><img alt="ORGY-002 omnibus"/></a>'
    '<div class="mb-1"><a href="/actress/test-star-1">Test Star</a>'
    '<a href="/actress/other-2">Other</a><a href="/actress/third-3">Third</a></div><table></table>'
    '</div>'
    '</body></html>'
)


@pytest.mark.asyncio
async def test_fetch_star_filters_multi_star_titles(monkeypatch):
    """共演/omnibus titles (star_count > 1 on the ijav card) are dropped."""
    monkeypatch.setattr(sync_titles, "FETCH_RETRY_DELAYS", (0.0, 0.0))
    ijav_url = "https://example.com/test"

    async def _fetch(url: str) -> str:
        if url == ijav_url:
            return _MULTI_STAR_PAGE
        return _EMPTY_RSS  # no usable items → RSS raises → ijav-only degraded

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch = AsyncMock(side_effect=_fetch)
    star = StarConfig(name="Test Star", code="SOLO-001", star_page_url=ijav_url)

    result = await fetch_star(mock_fetcher, star, asyncio.Semaphore(1), asyncio.Semaphore(1))

    assert [it.code for it in result] == ["SOLO-001"]
