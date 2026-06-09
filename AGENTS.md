<!-- From: /root/claw-stream/AGENTS.md -->
<!-- Scope: /root/claw-stream directory tree -->
# AGENTS.md — claw-stream Developer Guide

This file is the working guide for AI coding assistants. The reader should be treated as knowing nothing about this repository.

---

## 1. Project Overview

This repository is **claw-stream**, a personal workspace. The only active subproject is:

- **claw-stream** (`/root/claw-stream/`) — BitTorrent-based local video streaming system.

> Note: `Project_Soul_Anchor/` mentioned in `README.md` and `docs/README.md` does not exist in the current codebase (excluded by root `.gitignore`). All actual code lives at the project root.

### 1.1 Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11+ | FastAPI + uvicorn |
| BitTorrent | libtorrent 2.0.x | Session management, piece download, sparse files |
| Database | DuckDB 1.5.2+ | Single-file `data/claw.duckdb`, stores metadata and covers |
| Frontend | Nuxt 3.16+ / Vue 3.5+ | TypeScript + Tailwind CSS + Pinia |
| PWA | `@vite-pwa/nuxt` | Service Worker + manifest + offline caching |
| Deploy | systemd + Caddy | Caddy auto-provisions Let's Encrypt TLS |
| Package Mgmt | `uv` (Python) / `npm` (Node) | `pyproject.toml` + `frontend/package.json` |

### 1.2 Core Architecture Decisions

- **Sparse File + SEEK_DATA/SEEK_HOLE**: Linux sparse files for storage; un-downloaded regions occupy no disk. Stream reads use `SEEK_HOLE` to detect holes and avoid returning all-zero data to the browser.
- **Bootstrap-first verification**: Finished torrents are first scanned with `lseek(SEEK_HOLE)`; if data is complete, skip the minute-long hash recheck.
- **Tiered cache (L1/L2/L3/L4)**: Scores based on playback heat, completion, and access time, replacing pure LRU eviction.
- **PieceStateTracker**: Independent piece state machine (`backend/services/piece_tracker.py`). libtorrent `have_piece()` is unreliable during `checking_files`, so we track with bitmaps.
- **On-demand download**: Only download head + tail + current window (±30 pieces); all other pieces priority = 0.
- **Thread pool expansion**: Default thread pool expanded to 32 workers in `backend/main.py` lifespan to prevent blocking I/O from overwhelming the event loop.

### 1.3 Core Components

| Component | File | Responsibility |
|---|---|---|
| `TorrentEngine` | `backend/services/torrent_engine.py` | libtorrent session lifecycle, cache management, tiered eviction, moov scan |
| `PieceStateTracker` | `backend/services/piece_tracker.py` | 3×int bitmap state machine, O(1) POPCNT query |
| `video_stream` | `backend/services/video_stream.py` | Range request streaming, hole detection, seek priority adjustment |
| `stream_router` | `backend/routers/stream.py` | `/stream/{hash}` video stream, `/api/check/{hash}` status check |
| `torrents_router` | `backend/routers/torrents.py` | `/torrent/add`, `/torrent/status/{hash}`, seek/progress |
| `stars_router` | `backend/routers/stars.py` | `/api/stars` (aggregate actor/title/post data) |
| `cache_router` | `backend/routers/cache.py` | `/api/cache`, `/api/cache/metrics` |
| `sync_router` | `backend/routers/sync.py` | `/api/stars/sync` (background trigger for `scrapers/search_news.py`) |
| `auth_router` | `backend/routers/auth.py` | `/api/auth` (daily rotating password validation) |
| `log_router` | `backend/routers/log.py` | Log query endpoints |
| `test_router` | `backend/routers/test_helper.py` | Test helper endpoints (debug only) |

---

## 2. Directory Structure

```
/root/claw-stream/
├── backend/
│   ├── main.py              # FastAPI entry, lifespan, middleware, health/cover
│   ├── routers/             # API routes (stream, torrents, stars, cache, auth, log, sync, test_helper)
│   ├── services/            # Core business logic (torrent_engine, piece_tracker, video_stream)
│   ├── models/              # Pydantic models (Star, TorrentStatus, StreamCheckResponse, CacheMetrics…)
│   ├── bench/               # Performance benchmark scripts (bench_moov_scan, bench_piece_tracker)
│   └── regression/          # Internal regression tests (piece_tracker, torrent_engine_readd)
├── core/
│   ├── logger.py            # Shared logging (RotatingFileHandler + JSON/text format + trace_id)
│   ├── log_viewer.py        # Log query CLI (tail / grep / follow)
│   └── db/                  # DuckDB connection, schema, CRUD, ops_log
├── frontend/
│   ├── app.vue              # Nuxt root component
│   ├── layouts/default.vue  # Default layout
│   ├── pages/               # index.vue (home), login.vue (login)
│   ├── components/          # Vue components (StarCard, VideoModal, CachePanel, TitleCard, StarNav)
│   ├── composables/         # useApi, useStars, useVideoPlayer, useCachePreheat, useLogger
│   ├── middleware/          # auth.global.ts (cookie auth guard)
│   ├── assets/css/main.css  # Global styles
│   ├── types/api.ts         # TypeScript interface definitions
│   ├── nuxt.config.ts       # Nuxt 3 + PWA + Nitro proxy config
│   ├── tailwind.config.ts   # Tailwind config
│   └── package.json         # Frontend dependencies
├── scrapers/                # Playwright scrapers (search_news, fetch_jable, fetch_social, base)
├── tests/                   # Regression tests (pytest/unittest + local BT fixture)
├── scripts/                 # Ops scripts (run.sh, fix_bad_covers.py)
├── deploy/                  # systemd unit files
├── config.json              # Actor list config (stars[].code / handle / star_page_url)
├── pyproject.toml           # Python dependencies (uv managed)
├── Caddyfile                # Reverse proxy config (443 → backend:8765 / frontend:3000)
├── .gitignore               # Excludes data/, cache/, logs/, .venv/, node_modules/
└── logs/                    # Runtime log directory (per-module files, 10MB rollover)

docs/
├── README.md                # Project overview
└── star-archive/            # Detailed docs (architecture, tiered-cache, bootstrap-first…)
```

---

## 3. Database Schema (DuckDB)

- `stars` — Actor base info (`name`, `jp_name`, `handle`, `code`, `type`, `note`)
- `titles` — Title metadata (`code`, `title`, `release_date`, `views`, `likes`, `resolution`, `cover_b64`, `jable_m3u8`, `release_date_sort`, `charming_intro`…)
- `magnets` — Magnet links for titles (`hash`, `is_primary`)
- `social_posts` — Social platform posts (`platform`, `content`, `post_url`, `posted_at`)

Database file at `data/claw.duckdb`, **excluded by `.gitignore`, never commit to git**.

---

## 4. Build & Run

### 4.1 Dependency Installation

Using `uv` for Python dependencies:

```bash
cd /root/claw-stream
uv sync   # or uv pip install -e .
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

# Frontend (dev mode, Nitro auto-proxies /api /stream /torrent to localhost:8765)
cd /root/claw-stream/frontend
npm run dev
```

**Option 2: One-click script**

```bash
cd /root/claw-stream
./scripts/run.sh   # Starts both backend (8765) and frontend (3000)
```

### 4.3 Production Deployment

```bash
# Build frontend SSR artifacts
cd /root/claw-stream/frontend
npm run build

# Start via systemd
systemctl restart star-archive-backend
systemctl restart star-archive-frontend
```

See `deploy/star-archive-backend.service` and `deploy/star-archive-frontend.service`.

### 4.4 Caddy Reverse Proxy

Caddy listens on 443, auto-provisions Let's Encrypt certificates:
- `/api/*`, `/stream/*`, `/torrent/*`, `/cache/*` → `localhost:8765`
- Everything else → `localhost:3000` (Nuxt SSR)

Reload after config change:
```bash
systemctl reload caddy-claw
```

### 4.5 Port Allocation

| Port | Service | Notes |
|------|---------|-------|
| 80 | Caddy | HTTP-01 challenge |
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

### 5.2 SQL Safety

- **Never** concatenate user input directly into SQL
- Column identifiers must pass whitelist validation
- Value parameters use `?` placeholder binding

### 5.3 Git Commit Convention

- **One change, one commit**: each bug fix, feature, or refactor is a separate commit
- **Commit message format**:
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
| `tests/conftest.py` | Shared fixtures (`local_seed`, `real_video_engine`) | Local BT seed |
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
```

> Tests auto-skip when real cache files or local seeds are unavailable — they do not fail.

---

## 7. Security & Operations

### 7.1 Data Security

- DuckDB is a **single-file database** (`data/claw.duckdb`), excluded by `.gitignore`. **Never commit database files to git**
- Cover images may contain private content; agents must not leak them in shared contexts

### 7.2 Service Restart Rules

**Restart both services after any frontend or backend code change.**

```bash
systemctl restart star-archive-backend
systemctl restart star-archive-frontend
```

- `backend/main.py` runs via uvicorn; code hot-reload does not work.
- Frontend is served by Nuxt SSR (production build in `.output/`); changes require `npm run build` + service restart to take effect.

### 7.3 Authentication

- Frontend login uses daily rotating password: format `rn{YYMMDD}{day % 2}` (e.g. `rn2605111`)
- Auth validated via `/api/auth`, sets cookie `claw_auth=ok` on success
- Global route guard `frontend/middleware/auth.global.ts` checks this cookie, redirects to `/login` if missing

### 7.4 Logging System

- Backend log directory: `/root/claw-stream/logs/`
- Per-module files: `backend.log`, `backend-access.log`, `torrent-engine.log`, `video-stream.log`, `stream-router.log`, `piece-tracker.log`, `db-ops.log`, `sync.log`…
- 10MB rollover per file, keep 5 backups
- trace_id chain tracking (passed via HTTP header `x-trace-id`)
- Environment variable `LOG_JSON=1` switches to JSON output
- CLI log query tool: `python3 core/log_viewer.py tail -n 50`

### 7.5 Common Ops Commands

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
| Process Lifecycle | `docs/ops/process-lifecycle.md` | systemd service config & ops |
| HTTPS Setup | `docs/ops/https-setup.md` | Caddy + TLS |
| Safari Compatibility | `docs/analysis/safari-code4.md` | Safari code=4 root cause analysis |
| UI Design | `docs/design/ui-design.md` | Frontend design spec |
| Performance Optimization | `docs/analysis/piece-tracker-optimization.md` | Bitwise optimization records |
| Timeout Debug | `docs/analysis/timeout-debug.md` | Caddy 502 troubleshooting case |
| Logging | `docs/ops/tracing-logging.md` | Log files & troubleshooting |
| Deletion Design | `docs/design/deletion-design.md` | Safe actor deletion flow |
| Diff-Sync | `docs/design/diff-sync-design.md` | Incremental sync algorithm design |
| Project Refactor | `docs/skill/project-refactor.md` | Layout migration, terminology, english-ification log |

---

## 9. Red Lines

- Never leak private data (covers, database content, user memories)
- Never run destructive commands; `trash` > `rm`
- When uncertain, ask the user first
