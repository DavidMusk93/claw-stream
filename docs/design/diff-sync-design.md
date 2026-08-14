# Diff-Sync Design — Incremental Actor Title Synchronization

## 1. Background and Problem

The legacy `sync_titles.py` performed a **full scrape** on every run:

1. **Playwright browser rendering**: Each star required opening Chromium, loading the page, and waiting for DOM
2. **Full cover download**: Existing covers were re-downloaded from DMM CDN / ijavtorrent
3. **Full database UPSERT**: All titles (new + existing) executed `INSERT ... ON CONFLICT DO UPDATE`

For a typical set of stars (~50–80 titles each):

| Stage | Legacy Cost |
|---|---|
| Playwright fetching | 30–60 s |
| Cover download (400+ images) | 10–20 s |
| Database write (400+ rows) | 2–5 s |
| **Total** | **45–90 s** |

First principle: **update as fast as possible**. Full scraping is not the answer.

## 2. Core Insights

1. **Hybrid source: ijavtorrent primary + sukebei RSS supplement**: ijavtorrent actress pages carry the rich metadata (retail dates, views, likes, `cover_url`, hhd800-tagged magnets) but the listing has been sparse/capped since the 2026-08 catalog loss (no pagination; e.g. JULIA 61 cards vs 201 in DB). The sukebei.nyaa.si RSS search (`?page=rss&q={name}&s=id&o=desc`) corrects those gaps: structured XML, no browser required
2. **One RSS page covers diff-sync**: up to 75 items sorted by newest upload; parsing is fast (`xml.etree.ElementTree`, < 10 ms)
3. **New titles are rare**: Daily syncs usually add 0–5 titles
4. **Existing covers never change**: Re-downloading them is pure waste

## 3. Diff-Sync Algorithm

### Phase 0: Preload Local State

```sql
SELECT star_id, code FROM titles
```

- Loads into an in-memory `set[tuple[int, str]]` for O(1) lookups
- Implemented by `db.load_all_title_codes()`

### Phase 1: Incremental Page Fetching

**Replace Playwright with pure HTTP (`HttpxFetcher`)**

Per star, both sources are fetched concurrently (`fetch_star`):

- **ijavtorrent (primary)**: HTTP GET the actress page (`star_page_url`), parse cards with `IJavTorrentExtractor`. Integrity checks: closing `</html>` tag + non-zero parsed cards, retried up to `MAX_FETCH_ATTEMPTS = 3` times. There is no DB-count floor — listings are legitimately sparse since the catalog loss, so a low card count is not proof of a truncated transfer
- **sukebei RSS (supplement)**: HTTP GET the RSS search. All query variants per star (`sync_query`, `name`, `jp`) are fetched and **merged by code** — uploaders tag different spellings across torrents, so the first non-empty query is not enough (romaji-only results would miss titles tagged with just the Japanese name). Same-code candidates are deduped by magnet. Items not mentioning the star's name are dropped (RSS search is full-text and returns noise)
- **Merge** (`merge_sources`): ijavtorrent metadata wins (retail date, views, cover_url); magnet candidates are unioned (deduped by magnet); RSS-only codes are appended as corrections; `likes` = max of both
- **HD selection parity**: `+++ [FHD]` uploads are flagged `is_hhd800` (the +1000 scoring bonus in `_score_magnet` makes them win), and VR resolution tags (`[8KVR]`/`[4KVR]`) score above plain 4K. Digit-led amateur codes (e.g. `229SCUTE-1575`) are recognized by `_CODE_RE`
- **Degradation**: one source failing degrades the star to the other with a loud warning; the star fails only when BOTH fail
- Diff against the in-memory set; keep only **new titles**

**Rate limiting**: sukebei answers bursts with HTTP 429. RSS fetches are capped at `RSS_MAX_CONCURRENCY = 2` with `RSS_REQUEST_INTERVAL = 0.5 s` pacing; a 429 triggers a `RATE_LIMIT_RETRY_DELAY = 10 s` back-off.

**Field caveats for RSS-supplement titles** (codes that only sukebei returned):

- `release_date` is the torrent **upload date** (`pubDate`), not the retail release date (ijavtorrent and legacy rows keep retail dates; sorting still works)
- `views` is unavailable (`None`); `likes` = max `nyaa:downloads` among the title's torrents
- `cover_url` is unavailable; covers rely on the DMM CDN by-code fallback in `cover_utils.py` (`_dmm_candidate_urls`): physical `mono/movie/adult` ids (bare + `118` maker prefix, e.g. Prestige `118abf367`) and digital/VR `digital/video` ids with the number zero-padded to 5 digits plus maker prefixes (e.g. `sivr00490`, `1favr00002`, `13dsvr01669`). Unknown DMM ids redirect to a `now_printing.jpg` placeholder with HTTP 200 — the fallback detects it via the final URL and skips it.

**Why parse all titles?**

- 50-title HTML parsing is negligible (< 10 ms)
- The real bottleneck is network I/O and cover download
- Pages are not strictly ordered by date, so "stop at first existing" logic is unreliable

### Phase 2: Incremental Cover Download

Only download covers for new titles:

```python
cover_items = [(it.code, it.cover_url or "") for it in new_items]
cover_map = await download_covers_batch(cover_items, concurrency=8)
```

- Daily sync drops from 400+ covers to 0–5 covers
- Time drops from 10–20 s to **< 1 s**

**Existing cover protection**:

- `upsert_title` in `core/db/crud.py` preserves `cover_b64` when an update receives `cover_b64=None`
- `TitleSyncSink.write_batch` omits `cover_b64` from the `ON CONFLICT DO UPDATE` column list, so existing covers are never overwritten during incremental refresh

### Phase 3: Incremental Database Write

Only INSERT new titles; skip existing ones entirely:

```python
# Old logic: all items UPSERT
# New logic: sink.write_batch(new_items, new_codes, cover_map)
```

- Daily writes drop from 400+ rows to 0–5 rows
- Time drops from 2–5 s to **< 50 ms**

## 4. Performance Expectations

| Stage | Full Sync | Diff-Sync | Speedup |
|---|---|---|---|
| Page fetching | 30–60 s (Playwright) | 2–5 s (HTTP) | **10–15×** |
| Cover download | 10–20 s (400+ covers) | < 1 s (0–5 covers) | **20×+** |
| Database write | 2–5 s (400+ rows) | < 50 ms (0–5 rows) | **50×+** |
| **Total** | **45–90 s** | **3–6 s** | **15–20×** |

## 5. Edge Cases

| Case | Handling |
|---|---|
| **First sync (empty database)** | `new_items = all_items`; degenerates to full sync, but HTTP is still ~10× faster than Playwright |
| **Large batch (> 20 new titles)** | `MAX_NEW_TITLES = 20` caps ingestion per star to avoid overwhelming the system |
| **Cover download failure** | `upsert_title` and `write_batch` preserve existing `cover_b64`; missing new covers result in empty strings, not data loss |
| **HTTP interception / Cloudflare** | No automatic fallback; both sources use `HttpxFetcher` (pure HTTP) |
| **Add star** | `POST /api/stars/add` takes an ijavtorrent actress page URL, parses name/code from the page, then background-syncs via the same hybrid pipeline (`sync_star`) |
| **Star fetch failure** | A star fails only when ijavtorrent AND sukebei both fail (`fetch_star` raises); one source down degrades to the other with a loud warning. `run()` collects per-star failures in `{"results", "failed"}`; if **every** star fails, `run()` raises `RuntimeError` so the web UI reports a sync error instead of a fake "0 new titles / All caught up" success |
| **Truncated/empty response** | ijavtorrent pages must end with `</html>` and parse ≥1 card; RSS bodies must end with `</rss>` and parse as XML. Violations are retried up to `MAX_FETCH_ATTEMPTS = 3` times (backoff `FETCH_RETRY_DELAYS = (1s, 2s)`, `RATE_LIMIT_RETRY_DELAY = 10s` after HTTP 429). An RSS search where every query variant (`sync_query`, `name`, `jp` — all fetched and merged) yields zero usable items raises `IncompletePageError` — never a fake "no new titles" |
| **Star not searchable under its display name** | Uploaders may tag a different name spelling than `config.json`'s `name` (e.g. 美ノ瀬すずめ → 美乃すずめ, Komatsu Sora → 小松空, Itsuki Yukimura → series code `229SCUTE`). Set `sync_query` to the spelling sukebei actually uses; without it the star loses the RSS supplement (degraded warning each sync) but ijavtorrent still covers her |

## 6. Implementation Plan

| Step | File | Status |
|---|---|---|
| Replace `PlaywrightFetcher` with `HttpxFetcher` | `scrapers/v2/tasks/sync_titles.py` | ✅ Done |
| Cover download only for `new_items` | `scrapers/v2/tasks/sync_titles.py` | ✅ Done |
| Preserve existing `cover_b64` in UPSERT | `core/db/crud.py` + `scrapers/v2/sinks.py` | ✅ Done |
| Add `tests/test_diff_sync.py` | `tests/test_diff_sync.py` | ✅ Done |
| HTTP vs Playwright benchmark | — | ❌ Not implemented |

## 7. Related Files

| File | Responsibility |
|---|---|
| [`scrapers/v2/tasks/sync_titles.py`](../../scrapers/v2/tasks/sync_titles.py) | Main diff-sync orchestration (`run`, `sync_star`, `fetch_star` hybrid merge) |
| [`scrapers/v2/fetchers.py`](../../scrapers/v2/fetchers.py) | `HttpxFetcher` implementation |
| [`scrapers/v2/extractors.py`](../../scrapers/v2/extractors.py) | `IJavTorrentExtractor` (actress pages, primary source) + `SukebeiRssExtractor` (RSS/XML, supplement) |
| [`scrapers/v2/sinks.py`](../../scrapers/v2/sinks.py) | `TitleSyncSink.write_batch` (batch UPSERT) |
| [`core/db/crud.py`](../../core/db/crud.py) | `load_all_title_codes`, `upsert_title` |
| [`tests/test_diff_sync.py`](../../tests/test_diff_sync.py) | Diff logic regression tests |

See also:
- [Cache Architecture](cache-architecture.md) — data ingestion pipeline
- [Tiered Cache](tiered-cache.md) — storage and eviction policy
