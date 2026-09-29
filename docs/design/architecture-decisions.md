# Architecture Decisions

Core architectural decisions and their rationale. These are load-bearing — do
not violate them without updating the matching regression tests and this file.

## Storage & Streaming

- **Sparse File + SEEK_DATA/SEEK_HOLE**: Linux sparse files for storage; un-downloaded regions occupy no disk. Stream reads use `SEEK_HOLE` to detect holes and avoid returning all-zero data to the browser.
- **Bootstrap-first verification + fast-resume snapshots**: Finished/seeding torrents are first scanned with `lseek(SEEK_HOLE)`; if data is complete, skip the minute-long hash recheck. Resume data (`{hash}.resume`, saved on pause and graceful shutdown) is merged at add time so a restart skips the full-file hash check entirely; the snapshot is invalidated whenever files change behind libtorrent's back (punch-hole, stale metadata). During an actual check, `check_progress` exposes libtorrent's native verification progress. See `docs/design/bootstrap-first.md`.
- **Disk is the single source of truth**: piece state is derived from on-disk data, not libtorrent's in-memory view (see `tests/test_disk_truth_source.py`).
- **PieceStateTracker**: Independent piece state machine (`backend/services/piece_tracker.py`). libtorrent `have_piece()` is unreliable during `checking_files`, so we track with bitmaps. See `docs/design/piece-tracker.md`.
- **Thread pool expansion**: Default thread pool expanded to 32 workers in `backend/main.py` lifespan to prevent blocking I/O from overwhelming the event loop.
- **Upload bandwidth cap**: libtorrent `upload_rate_limit = 2 MB/s` to reserve bandwidth for HTTP streaming.

## On-Demand Download Discipline (不看不下、看哪下哪)

Locked by `tests/test_torrent_engine_arch.py::TestOnDemandDownloadDiscipline`
(mock, fast) and `tests/test_ondemand_download.py` (live local seed). Contract:

- add = paused + all-zero priorities
- play/resume/progress = urgent(7) only inside ±30 window + moov, verified retained(1), everything else stop(0)
- seek = ±15 window, abandoned pieces drop to 0
- range requests raise only their own ±2 pieces
- `set_sequential_download(True)` is never allowed

Initial play window stays strict head+moov — widening it raises aggregate rate
but delays head completion.

## libtorrent Session Tuning

- **Cold-start tuning (bench-verified)**: `announce_to_all_trackers/tiers`, `connection_speed=50`, `peer_connect_timeout=8`, `request_timeout=20`, `piece_timeout=10` — first byte on a cold torrent dropped 10s → 5s. Phantom-finished (pause zeroes all priorities → libtorrent flips to finished) is escaped by raising priorities + resume, NOT by recheck (libtorrent 2.0 POSIX storage re-enters downloading; recheck at most once per add and only when verified>0 contradicts head_ready).
- **`close_redundant_connections=False` (critical for streaming)**: libtorrent's default drops ALL peer connections whenever a torrent enters finished ("torrent finished" disconnect), and a sliding-window torrent flips finished↔downloading constantly (window drains, pause, fresh all-zero-priority add). Dropped peers carry reconnect backoff that no API resets, so every flip used to cost seconds-to-minutes of peer recovery. With False, peers stay attached and re-raised priorities download immediately. `_set_stream_window` also fires `force_reannounce()` on phantom escape to pull fresh swarm peers fast.

## Cache

- **Tiered cache (L1/L2/L3/L4)**: Scores based on playback heat, completion, and access time, replacing pure LRU eviction. Liked titles (`user_liked=1`) are protected from eviction and auto-resumed on startup. See `docs/design/tiered-cache.md`.

## Data & Sync

- **SSE push replaces polling**: `core/events.py` in-process event bus + `GET /api/events` SSE stream (`sync.status`, `sync.progress` (per-phase: prepare/fetch/covers/write), `sync.resync_required`, `torrent.status`, `torrent.progress` (2s throttled, in-memory only; includes `piece_segments` — the 100-segment live download-state map rendered by the player progress bar), `cache.update`, `star.ready`); frontend consumes via `useEventSource.ts` with zero polling timers. Slow clients are coalesced (queue drain + `sync.resync_required`), never silently disconnected. See `docs/design/sse-push-architecture.md`.
- **Serial DB write queue (legacy)**: all DB writes go through `core/db/write_queue.py` (single worker coroutine). It dates from the DuckDB one-writer era; PostgreSQL handles concurrent writers, so this is a serialization choice kept to avoid touching many call sites, not a correctness requirement.
- **Storage migrated to PostgreSQL (2026-09-19)**: DuckDB's delete+append UPDATE semantics × inline ~200KB cover blobs caused ~700× write amplification (1.8G→15G in 3 months, disk full). PG stores blobs out of line (TOAST) and reclaims space via autovacuum. The old `data/claw.duckdb` is kept as a rollback archive.
- **Wide-table schema**: `titles` inlines `star_code`/`star_name`/magnet info (`magnet`, `magnet_hash`, `all_magnets JSONB`) — no stars-titles-magnets triple JOIN.
- **Disk-first cover pipeline**: covers exported to `images/titles/{code}/{code}.jpg` (plus a 400px-wide `{code}_thumb.jpg` for list/grid views and an 800px-wide `{code}_mid.jpg` for the hero srcset, generated on write and by `export_covers.py`) are served as static files by Caddy. `/api/stars` emits these static URLs directly (`cover_url`/`cover_thumb_url`/`cover_mid_url`) when the files exist; `/api/cover/{code}[?thumb=1]` is the fallback for titles missing on disk (DB blob + disk backfill).
- **Diff-Sync hybrid source: ijavtorrent primary + sukebei.nyaa.si RSS supplement**: title sync fetches the ijavtorrent actress page (rich metadata: retail dates, views, cover_url, hhd800 magnets) and merges sukebei RSS search results (all query variants `sync_query`/`name`/`jp` unioned by code) to correct ijav's catalog gaps — ijav metadata wins, magnets unioned, RSS-only codes appended; multi-star (共演/omnibus) and VR titles are filtered **at collection time** by `scrapers/v2/filters.py` (`star_count>1` actress-link count, 【VR】/[VR]/VR-label-prefix, 共演/オムニバス keywords, cast-list patterns, other-roster-star mention) — they never enter the DB. Titles without any valid magnet (no extractable 40-hex btih hash) are skipped at the sink (`scrapers/v2/sinks.py`): unplayable rows are never recorded. Covers taller than wide (`COVER_MAX_HW_RATIO = 1.0`) are vertical front-cover thumbnails, not usable covers — the blob is dropped, and a **new** title without a usable cover is skipped entirely (it stays "new" and retries next sync); existing rows keep their stored cover. `scripts/cleanup_bad_titles.py` applies the same rules to legacy rows. Sink-skipped codes are recorded in the `title_blacklist` table and filtered out before the diff in later syncs (`_drop_blacklisted`), so their covers are never re-downloaded; entries older than 7 days get one retry pass, and a successful write clears the entry (`clear_blacklist_entries`). ijav lost much of its catalog in 2026-08 and still serves sparse listings (no pagination). Since 2026-09-19 the RSS supplement runs as a **deferred background phase** under an adaptive global rate limiter (`SukebeiRateLimiter`): a soft-block streak or 429 backs it off exponentially, and RSS failures never fail the sync. See `docs/design/diff-sync-design.md`.
- **Auth on all API routers**: every router except `/api/auth` and `/api/test` uses `Depends(require_auth)` (cookie `claw_auth=ok`).
