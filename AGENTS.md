# AGENTS.md — claw-stream Developer Guide

This file is the working guide for AI coding assistants. The reader should be treated as knowing nothing about this repository.

---

## 1. Project Overview

This repository is **claw-stream**, a personal workspace. The only active subproject is:

- **claw-stream** (`/root/claw-stream/`) — BitTorrent-based local video streaming system (personal title tracking + stream-while-downloading player), internally also called "Star Archive".

> Note: `Project_Soul_Anchor/` mentioned in `README.md` and `docs/README.md` does not exist in the current codebase (excluded by root `.gitignore`). All actual code lives at the project root.

### 1.1 Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11+ | FastAPI + uvicorn |
| BitTorrent | libtorrent 2.0.x (2.0.11 in `.venv`) | Session management, piece download, sparse files |
| Database | DuckDB 1.5.2+ | Single-file `data/claw.duckdb`, stores metadata and cover blobs |
| Frontend | Nuxt 3.16+ / Vue 3.5+ | TypeScript + Tailwind CSS + Pinia |
| PWA | `@vite-pwa/nuxt` | Service Worker + manifest + runtime caching |
| Scraping | httpx + Playwright + selectolax | `scrapers/v2/` pipeline; Playwright only as fallback |
| Deploy | systemd + Caddy | Caddy auto-provisions Let's Encrypt TLS |
| Package Mgmt | `uv` (Python) / `npm` (Node) | `pyproject.toml` + `frontend/package.json` |

> **libtorrent is NOT in `pyproject.toml`/`uv.lock`.** It is installed directly into `.venv`
> (`uv pip install libtorrent`). After `uv sync` on a fresh machine you must install it again.

### 1.2 Core Architecture Decisions

- **Sparse File + SEEK_DATA/SEEK_HOLE**: Linux sparse files for storage; un-downloaded regions occupy no disk. Stream reads use `SEEK_HOLE` to detect holes and avoid returning all-zero data to the browser.
- **Bootstrap-first verification**: Finished torrents are first scanned with `lseek(SEEK_HOLE)`; if data is complete, skip the minute-long hash recheck.
- **Disk is the single source of truth**: piece state is derived from on-disk data, not libtorrent's in-memory view (see `tests/test_disk_truth_source.py`).
- **Tiered cache (L1/L2/L3/L4)**: Scores based on playback heat, completion, and access time, replacing pure LRU eviction. Liked titles (`user_liked=1`) are protected from eviction and auto-resumed on startup.
- **PieceStateTracker**: Independent piece state machine (`backend/services/piece_tracker.py`). libtorrent `have_piece()` is unreliable during `checking_files`, so we track with bitmaps.
- **On-demand download**: Only download head + tail + current window (±30 pieces); all other pieces priority = 0.
- **Thread pool expansion**: Default thread pool expanded to 32 workers in `backend/main.py` lifespan to prevent blocking I/O from overwhelming the event loop.
- **SSE push replaces polling**: `core/events.py` in-process event bus + `GET /api/events` SSE stream (`sync.status`, `sync.resync_required`, `torrent.status`, `torrent.progress` (2s throttled, in-memory only), `cache.update`, `star.ready`); frontend consumes via `useEventSource.ts` with zero polling timers. Slow clients are coalesced (queue drain + `sync.resync_required`), never silently disconnected. See `docs/design/sse-push-architecture.md`.
- **DuckDB serial write queue**: all DB writes go through `core/db/write_queue.py` (single worker coroutine), eliminating DuckDB's one-writer lock conflicts.
- **Wide-table schema**: `titles` inlines `star_code`/`star_name`/magnet info (`magnet`, `magnet_hash`, `all_magnets JSON`) — no stars-titles-magnets triple JOIN.
- **Disk-first cover pipeline**: covers exported to `images/titles/{code}/{code}.jpg` are served as static files by Caddy; `/api/cover/{code}` falls back to the DB blob and backfills disk.
- **Upload bandwidth cap**: libtorrent `upload_rate_limit = 2 MB/s` to reserve bandwidth for HTTP streaming.
- **Diff-Sync hybrid source: ijavtorrent primary + sukebei.nyaa.si RSS supplement**: title sync fetches the ijavtorrent actress page (rich metadata: retail dates, views, cover_url, hhd800 magnets) and merges sukebei RSS search results (all query variants `sync_query`/`name`/`jp` unioned by code) to correct ijav's catalog gaps — ijav metadata wins, magnets unioned, RSS-only codes appended; multi-star (共演/omnibus) titles filtered via the card's actress-link count. A star fails only when BOTH sources fail. ijav lost much of its catalog in 2026-08 and still serves sparse listings (no pagination). See `docs/design/diff-sync-design.md`.
- **Auth on all API routers**: every router except `/api/auth` and `/api/test` uses `Depends(require_auth)` (cookie `claw_auth=ok`).

### 1.3 Core Components

| Component | File | Responsibility |
|---|---|---|
| `TorrentEngine` | `backend/services/torrent_engine.py` | libtorrent session lifecycle, cache management, tiered eviction, moov scan, liked-set protection, orphan GC |
| `PieceStateTracker` | `backend/services/piece_tracker.py` | 3×int bitmap state machine, O(1) POPCNT query |
| `video_stream` | `backend/services/video_stream.py` | Range request streaming, hole detection, seek priority adjustment |
| `stream_router` / `check_router` | `backend/routers/stream.py` | `/stream/{hash}` video stream, `/api/check/{hash}` status check |
| `torrents_router` | `backend/routers/torrents.py` | `/torrent/add`, `/torrent/status/{hash}`, seek/progress/pause/resume |
| `stars_router` | `backend/routers/stars.py` | `/api/stars` list/add/delete/like |
| `cache_router` | `backend/routers/cache.py` | `/api/cache`, `/api/cache/metrics`, delete, gc-orphans |
| `sync_router` | `backend/routers/sync.py` | `/api/stars/sync` — runs `scrapers.v2.tasks.sync_titles` in-process (async, no subprocess) |
| `auth_router` | `backend/routers/auth.py` | `/api/auth` (daily rotating password validation) |
| `log_router` | `backend/routers/log.py` | `/api/log` log query endpoints |
| `events_router` | `backend/routers/events.py` | `/api/events` SSE stream (heartbeat every 30s) |
| `test_router` | `backend/routers/test_helper.py` | Test helper endpoints (debug only, no auth) |
| `EventBus` | `core/events.py` | In-process async pub/sub for SSE |
| `DuckDBWriteQueue` | `core/db/write_queue.py` | Serializes all DuckDB writes in-process |
| Scraper pipeline | `scrapers/v2/` | `tasks/sync_titles.py` orchestrates sources → fetchers (httpx/Playwright) → extractors → sinks (DuckDB) |

---

## 2. Directory Structure

```
/root/claw-stream/
├── backend/
│   ├── main.py              # FastAPI entry, lifespan, middleware, /api/health, /api/cover/{code}
│   ├── routers/             # API routes (stream, torrents, stars, cache, auth, log, sync, events, test_helper)
│   ├── services/            # Core business logic (torrent_engine, piece_tracker, video_stream)
│   ├── models/              # Pydantic models (star, torrent, stream, cache, work)
│   ├── bench/               # Performance benchmarks (bench_moov_scan, bench_piece_tracker)
│   └── regression/          # Internal regression tests (piece_tracker, torrent_engine_readd)
├── core/
│   ├── logger.py            # Shared logging (RotatingFileHandler + JSON/text format + trace_id)
│   ├── log_viewer.py        # Log query CLI (tail / grep / follow)
│   ├── events.py            # In-process event bus for SSE push
│   └── db/                  # DuckDB: connection, schema, crud, queries, write_queue, ops_log
│                            # CLI: python3 -m core.db [backfill|stats]
├── frontend/
│   ├── app.vue              # Nuxt root component
│   ├── layouts/default.vue  # Default layout
│   ├── pages/               # index.vue (home), login.vue (login)
│   ├── components/          # Vue components grouped: cache/, star/, title/, ui/, video/
│   ├── composables/         # useApi, useStars, useVideoPlayer, useCachePreheat, useEventSource, useLogger
│   ├── middleware/          # auth.global.ts (cookie auth guard)
│   ├── assets/css/main.css  # Global styles
│   ├── types/api.ts         # TypeScript interface definitions
│   ├── nuxt.config.ts       # Nuxt 3 + PWA + Nitro proxy config
│   ├── tailwind.config.ts   # Tailwind config
│   └── package.json         # Frontend dependencies
├── scrapers/
│   ├── search_news.py       # Compatibility shim → delegates to scrapers/v2/
│   └── v2/                  # Pipeline: cli, pipeline, schemas, sources, fetchers, extractors,
│                            #   sinks, cover_utils, tasks/sync_titles.py
├── tests/                   # Regression tests (pytest + local BT fixture in tests/fixtures/)
├── scripts/                 # Ops scripts (run.sh, export_covers.py, fill_all_covers.py,
│                            #   fix_bad_covers.py, fix_missing_covers.py)
├── deploy/                  # systemd unit files (star-archive-backend, star-archive-frontend)
├── config/
│   └── mcporter.json        # MCP server config (exa) for AI agent tooling
├── config.json              # Actor list config: {title, sort_by, stars[{name, jp, handle, code, type, star_page_url}]}
├── pyproject.toml           # Python dependencies (uv managed) — libtorrent NOT included, see §1.1
├── Caddyfile                # Reverse proxy config (443 → backend:8765 / frontend:3000)
├── refresh.sh               # One-shot data refresh: scrapers/search_news.py → DuckDB + stats
├── fetch-covers.sh          # Parallel cover download from DMM/FANZA CDN → /tmp/star-covers
├── b64-encode.sh            # Cover images → base64 text files
├── _test_ddg.py             # Ad-hoc duckduckgo-search smoke script (not part of test suite)
├── package.json             # VESTIGIAL ("star-collection", main=generate-report.js which no
│                            #   longer exists) — frontend deps live in frontend/package.json
├── .gitignore               # Excludes data/, cache/, logs/, images/titles/, .venv/, node_modules/
└── logs/                    # Runtime log directory (per-module files, 10MB rollover × 5)

docs/
├── README.md                # Documentation index (domain-organized)
├── design/                  # architecture, cache-architecture, tiered-cache, bootstrap-first,
│                            #   piece-tracker, deletion-design, diff-sync-design,
│                            #   sse-push-architecture, ui-design
├── ops/                     # process-lifecycle, https-setup, tracing-logging
├── analysis/                # safari-code4, finished-deadlock-allzero-false-positive,
│                            #   piece-tracker-optimization, timeout-debug
└── skill/                   # project-refactor
```

---

## 3. Database Schema (DuckDB)

Wide-table design (`core/db/schema.py`, idempotent `init_schema()` with `ALTER TABLE` backfills):

- `stars` — Actor base info (`name` UNIQUE, `jp_name`, `handle`, `code`, `type`, `note`)
- `titles` — Title metadata, inlines star and magnet info: `star_id`, `star_code`, `star_name`, `code`, `title`, `release_date`, `release_date_sort`, `views`, `likes`, `resolution`, `cover_url`, `cover_b64`, `cover_path`, `charming_intro`, `jable_m3u8`, `magnet`, `magnet_hash`, `all_magnets JSON`, `user_liked INTEGER DEFAULT 0`; `UNIQUE(star_id, code)`
- `social_posts` — Social platform posts (`star_id`, `platform`, `content`, `post_url`, `posted_at`)

There is **no separate `magnets` table** anymore — magnet data lives on `titles`.

Database file at `data/claw.duckdb`, **excluded by `.gitignore`, never commit to git**.

DB CLI: `python3 -m core.db` (init schema) · `python3 -m core.db backfill` · `python3 -m core.db stats`.

---

## 4. Build & Run

### 4.1 Dependency Installation

Using `uv` for Python dependencies:

```bash
cd /root/claw-stream
uv sync   # or uv pip install -e .
uv pip install libtorrent   # NOT in pyproject.toml — install separately (see §1.1)
```

Frontend dependencies:

```bash
cd /root/claw-stream/frontend
npm install
```

### 4.2 Local Development

**Option 1: Manual separate start**

```bash
# Backend (PYTHONPATH must point to project root)
cd /root/claw-stream
PYTHONPATH=/root/claw-stream \
  uv run python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765

# Frontend (dev mode, Nitro proxies /api /stream /torrent /images to localhost:8765)
cd /root/claw-stream/frontend
npm run dev
```

**Option 2: One-click script (dev mode with auto-restart + reload)**

```bash
cd /root/claw-stream
./scripts/run.sh   # Backend (8765, uvicorn --reload) + Nuxt dev (3000, HMR)
```

### 4.3 Production Deployment

```bash
# Build frontend SSR artifacts
cd /root/claw-stream/frontend
npm run build

# Start via systemd (see deploy/*.service)
systemctl restart star-archive-backend    # .venv/bin/python -m uvicorn ... :8765
systemctl restart star-archive-frontend   # node .output/server/index.mjs :3000
```

In production the Nitro server also proxies `/api`, `/stream`, `/torrent`, `/images` to `127.0.0.1:8765` via `routeRules` in `nuxt.config.ts`.

### 4.4 Caddy Reverse Proxy

Caddy serves `cc.guohuasun.com` on 443, auto-provisions Let's Encrypt certificates:
- `/images/*` → served directly from disk bind mount `/var/lib/caddy/claw-images`
  (requires `mount --bind /root/claw-stream/images /var/lib/caddy/claw-images`)
- `/api/*`, `/stream/*`, `/torrent/*`, `/cache/*` → `localhost:8765`
- Everything else → `localhost:3000` (Nuxt SSR, 10s dial / 30s response timeout)
- **HTTP/3 (QUIC) is disabled** — mobile networks throttle/drop UDP, causing 50–80s cover loads

Reload after config change:
```bash
systemctl reload caddy-claw
```

### 4.5 Port Allocation

| Port | Service | Notes |
|------|---------|-------|
| 80 | Caddy | HTTP-01 challenge, `/health` |
| 443 | Caddy | HTTPS reverse proxy |
| 3000 | Nuxt frontend | SSR rendering |
| 8765 | FastAPI backend | BitTorrent + HTTP API |

---

## 5. Code Style Guide

### 5.1 Python

- **Every file must start with** `from __future__ import annotations`
- Use Python 3.11+ type annotation syntax (e.g. `str | None`, `dict[str, Any]`)
- Class names, function names, variable names in **English**; **comments and docstrings in English**
- Prefer f-string for string formatting
- SQL parameter binding: prefer `?` placeholders; only VARIANT-type complex dicts may use `variant_sql_literal()` generated expressions
- All DuckDB writes go through `core/db/write_queue.py`; never open a second write connection ad hoc

### 5.2 SQL Safety

- **Never** concatenate user input directly into SQL
- Column identifiers must pass whitelist validation
- Value parameters use `?` placeholder binding

### 5.3 Git Commit Convention

- **One change, one commit**: each bug fix, feature, or refactor is a separate commit
- **Commit message format** (matches git history, e.g. `fix(engine): ...`, `feat: ...`, `perf: ...`, `docs: ...`):
  ```
  <type>: <short subject>  (<= 50 chars)

  <long details>  (why + what, wrap at 72 chars)
  ```
- **Don't batch changes**: commit immediately after fixing
- **Docs with code**: update docs in the same commit as code changes

---

## 6. Tests

### 6.1 Test Files

| File | Description | Dependencies |
|---|---|---|
| `tests/test_piece_tracker.py` | PieceStateTracker state machine & bootstrap tests | Based on `local_bt_fixture` local BT seed |
| `tests/test_piece_tracker_regression.py` | PieceStateTracker regression tests | Same as above |
| `tests/test_stream_regression.py` | FastAPI TestClient + real cache file full regression | Needs real cache file (e.g. SNOS-171), auto-skips otherwise |
| `tests/test_regression_video_stream.py` | Video stream pipeline regression (hole false-positives, memory explosion, finished deadlock) | Based on `local_bt_fixture` local BT seed |
| `tests/test_torrent_engine_arch.py` | TorrentEngine architecture tests (bootstrap-first, cache-warming) | No real download, uses mock |
| `tests/test_disk_truth_source.py` | "Disk is the single source of truth" regression (mocked libtorrent handle) | No network |
| `tests/test_diff_sync.py` | Diff-Sync incremental sync regression (sukebei RSS fetch, diff filtering, incremental covers, truncated-RSS/429 retry) | Mocked fetcher |
| `tests/conftest.py` | Shared fixtures (`local_seed`, `real_video_engine`) | Local BT seed |
| `tests/local_bt_fixture.py` + `tests/fixtures/` | Local seeder fixture (`test_video.mp4` + `test_video.torrent`) | — |
| `backend/regression/test_piece_tracker.py` | Internal piece tracker regression | — |
| `backend/regression/test_torrent_engine_readd.py` | Internal torrent engine regression | — |

### 6.2 Running Tests

```bash
cd /root/claw-stream

# All tests
uv run python -m pytest tests/ -v

# Individual
uv run python -m pytest tests/test_piece_tracker.py -v
uv run python -m pytest tests/test_piece_tracker_regression.py -v
uv run python -m pytest tests/test_torrent_engine_arch.py -v
uv run python -m pytest tests/test_stream_regression.py -v
uv run python -m pytest tests/test_regression_video_stream.py -v
uv run python -m pytest tests/test_disk_truth_source.py -v
uv run python -m pytest tests/test_diff_sync.py -v
```

> Tests auto-skip when real cache files or local seeds are unavailable — they do not fail.

---

## 7. Security & Operations

### 7.1 Data Security

- DuckDB is a **single-file database** (`data/claw.duckdb`), excluded by `.gitignore`. **Never commit database files to git**
- Cover images may contain private content; agents must not leak them in shared contexts
- `/cache` is intentionally **not** mounted as static files (prevents direct video download)
- CORS: origins from `CORS_ORIGINS` env var (default `localhost:3000`); `*` with credentials is deliberately unsupported

### 7.2 Service Restart Rules

**Restart both services after any frontend or backend code change.**

```bash
systemctl restart star-archive-backend
systemctl restart star-archive-frontend
```

- `backend/main.py` runs via uvicorn; code hot-reload does not work (dev reload only via `scripts/run.sh`).
- Frontend is served by Nuxt SSR (production build in `.output/`); changes require `npm run build` + service restart to take effect.

### 7.3 Authentication

- Frontend login uses daily rotating password: format `rn{YYMMDD}{day % 2}` (UTC date, e.g. `rn2605111`)
- Auth validated via `POST /api/auth`, which sets cookie `claw_auth=ok` server-side (so SSR sees it on the next request)
- `SECURE_COOKIES=1` env var enables the `Secure` cookie flag in production
- Global route guard `frontend/middleware/auth.global.ts` checks this cookie, redirects to `/login` if missing
- All backend routers except `/api/auth` and `/api/test` enforce `Depends(require_auth)`

### 7.4 Logging System

- Backend log directory: `/root/claw-stream/logs/` (override with `LOG_DIR` env var)
- Per-module files named after the logger: `backend.log`, `backend-access.log`, `torrent-engine.log`, `video-stream.log`, `stream-router.log`, `piece-tracker.log`, `db-ops.log`, `db-write-queue.log`, `sync.log`, `events.log`, `events-router.log`…
- 10MB rollover per file, keep 5 backups
- trace_id chain tracking (HTTP header `x-trace-id`; middleware generates one if absent)
- Environment variable `LOG_JSON=1` switches to JSON output
- CLI log query tool: `python3 core/log_viewer.py tail -n 50`

### 7.5 Data Refresh & Covers

```bash
# Refresh title data (scrapers v2 pipeline → DuckDB, then prints stats)
./refresh.sh [config.json]

# Export covers from DuckDB blobs to images/titles/{code}/{code}.jpg
.venv/bin/python scripts/export_covers.py

# Fetch actor covers from DMM/FANZA CDN and base64-encode them
./fetch-covers.sh [config.json]
./b64-encode.sh [image-dir]
```

`/api/cover/{code}` is the canonical cover URL: disk static file (307 redirect) → in-memory LRU → DuckDB blob (+ disk backfill). New titles never 404 even if the disk export lagged.

### 7.6 Common Ops Commands

```bash
# View backend logs
journalctl -u star-archive-backend -f

# Check torrent status
curl -s http://localhost:8765/torrent/status/<hash> | python3 -m json.tool

# Check sparse file real size
stat --format="logical=%s actual=%b*%B=%B" /root/claw-stream/cache/torrent/<hash>/.../*.mp4

# View cache metrics
curl -s http://localhost:8765/api/cache/metrics | python3 -m json.tool

# Health check
curl -s http://localhost:8765/api/health

# DB stats
python3 -m core.db stats
```

---

## 8. Documentation Index

| Document | Path | Content |
|----------|------|---------|
| System Architecture | `docs/design/architecture.md` | Playback flow, state machine, component interaction, troubleshooting |
| Cache Architecture | `docs/design/cache-architecture.md` | Cache module first principles, lifecycle, eviction strategy, best practices |
| Tiered Cache | `docs/design/tiered-cache.md` | L1/L2/L3/L4 cache policy & scoring formula |
| Bootstrap-first | `docs/design/bootstrap-first.md` | Skip recheck verification mechanism |
| Piece Tracker | `docs/design/piece-tracker.md` | Bitmap state machine architecture |
| SSE Push | `docs/design/sse-push-architecture.md` | Event bus + SSE replacing frontend polling |
| Process Lifecycle | `docs/ops/process-lifecycle.md` | systemd service config & ops |
| HTTPS Setup | `docs/ops/https-setup.md` | Caddy + TLS |
| Logging | `docs/ops/tracing-logging.md` | Log files & troubleshooting |
| Safari Compatibility | `docs/analysis/safari-code4.md` | Safari code=4 root cause analysis |
| Finished Deadlock RCA | `docs/analysis/finished-deadlock-allzero-false-positive.md` | All-zero false positive in hole detection |
| Performance Optimization | `docs/analysis/piece-tracker-optimization.md` | Bitwise optimization records |
| Timeout Debug | `docs/analysis/timeout-debug.md` | Caddy 502 troubleshooting case |
| UI Design | `docs/design/ui-design.md` | Frontend design spec |
| Deletion Design | `docs/design/deletion-design.md` | Safe actor deletion flow |
| Diff-Sync | `docs/design/diff-sync-design.md` | Incremental sync algorithm design |
| Project Refactor | `docs/skill/project-refactor.md` | Layout migration, terminology, english-ification log |

---

## 9. Memory & Handoff (nmem)

**nmem (Nowledge Mem MCP) is the sole memory system.** It is not optional context — it is where project knowledge lives across sessions.

- **Before starting work**: query nmem first — `read_working_memory` for the daily briefing, `memory_search` for prior decisions, lessons, design specs, and handoffs relevant to the task.
- **After producing durable knowledge** (root causes, design decisions, verified procedures, handoff state): write it to nmem with `memory_add` (proper `unit_type`: decision / procedure / learning / plan), and keep AGENTS.md + `docs/` in sync in the same change.
- Do not rely on in-conversation context as the only record — if it is worth remembering, it goes to nmem.

---

## 10. Red Lines

- Never leak private data (covers, database content, user memories)
- Never run destructive commands; `trash` > `rm`
- When uncertain, ask the user first
