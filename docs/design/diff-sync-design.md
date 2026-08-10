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

1. **sukebei.nyaa.si RSS search is structured XML**: no HTML scraping or browser required. Since 2026-08 it is the sync source — the previous source, ijavtorrent.com, lost most of its catalog (actress pages and site search silently dropped titles, e.g. ABF-338 vanished), which permanently tripped the truncated-page guard on every star
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

- HTTP GET retrieves the star's sukebei RSS search (`?page=rss&q={name}&s=id&o=desc`)
- Query fallback order per star: `sync_query` (config override) → `name` → `jp`; the first query yielding usable items wins
- Extract titles (`SukebeiRssExtractor`): group torrent rows by code into `MagnetCandidate`s, build magnets from `nyaa:infoHash`, map `nyaa:size/seeders/leechers/downloads/pubDate` to the existing `VideoItem` fields; items not mentioning the star's name are dropped (RSS search is full-text and returns noise)
- Diff against the in-memory set; keep only **new titles**

```python
rss = await fetcher.fetch(SUKEBEI_RSS_URL.format(q=quote(query)))
items = SukebeiRssExtractor().extract(rss, star_names={star.name, star.jp, star.sync_query})
new_items = [it for it in items if (star_id, it.code) not in existing_codes]
```

**Rate limiting**: sukebei answers bursts with HTTP 429. RSS fetches are capped at `RSS_MAX_CONCURRENCY = 2` with `RSS_REQUEST_INTERVAL = 0.5 s` pacing; a 429 triggers a `RATE_LIMIT_RETRY_DELAY = 10 s` back-off.

**Field caveats vs. ijavtorrent**:

- `release_date` is the torrent **upload date** (`pubDate`), not the retail release date (legacy rows keep retail dates; sorting still works)
- `views` is unavailable (`None`); `likes` = max `nyaa:downloads` among the title's torrents
- `cover_url` is unavailable; covers rely on the DMM CDN by-code fallback in `cover_utils.py` (including the `118`-prefixed maker variant, e.g. Prestige `118abf367`)

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
| **HTTP interception / Cloudflare** | No automatic fallback in the batch sync path; single-star background sync in `backend/routers/stars.py` still uses `PlaywrightFetcher` when needed |
| **Star RSS fetch failure** | `fetch_star_rss` re-raises; `run()` collects per-star failures and returns them in `{"results", "failed"}`. If **every** star fetch fails (e.g. source site unreachable), `run()` raises `RuntimeError` so the web UI reports a sync error instead of a fake "0 new titles / All caught up" success |
| **Truncated/empty RSS** | `fetch_star_rss` validates the body ends with `</rss>` and parses as XML; truncated or unparseable responses are retried up to `MAX_FETCH_ATTEMPTS = 3` times (backoff `FETCH_RETRY_DELAYS = (1s, 2s)`, `RATE_LIMIT_RETRY_DELAY = 10s` after HTTP 429). If every query variant (`sync_query` → `name` → `jp`) yields zero usable items, the star raises `IncompletePageError` and lands in the `failed` list — never as a fake "no new titles" |
| **Star not searchable under its display name** | Uploaders may tag a different name spelling than `config.json`'s `name` (e.g. 美ノ瀬すずめ → 美乃すずめ, Komatsu Sora → 小松空). Set `sync_query` to the spelling sukebei actually uses; without it the star fails explicitly every sync |

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
| [`scrapers/v2/tasks/sync_titles.py`](../../scrapers/v2/tasks/sync_titles.py) | Main diff-sync orchestration (`run`, `sync_star`) |
| [`scrapers/v2/fetchers.py`](../../scrapers/v2/fetchers.py) | `HttpxFetcher` implementation |
| [`scrapers/v2/extractors.py`](../../scrapers/v2/extractors.py) | `SukebeiRssExtractor` (RSS/XML, sync source) + `IJavTorrentExtractor` (legacy `selectolax` parser) |
| [`scrapers/v2/sinks.py`](../../scrapers/v2/sinks.py) | `TitleSyncSink.write_batch` (batch UPSERT) |
| [`core/db/crud.py`](../../core/db/crud.py) | `load_all_title_codes`, `upsert_title` |
| [`tests/test_diff_sync.py`](../../tests/test_diff_sync.py) | Diff logic regression tests |

See also:
- [Cache Architecture](cache-architecture.md) — data ingestion pipeline
- [Tiered Cache](tiered-cache.md) — storage and eviction policy
