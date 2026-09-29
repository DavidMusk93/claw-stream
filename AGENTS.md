# AGENTS.md — xstream Developer Guide

Entry point for AI coding assistants. Read this file fully; it is short on
purpose. Detail lives in `docs/` — follow the pointers before editing an area.

---

## 1. Project Overview

**xstream** (`/root/xstream/`) is a BitTorrent-based local video
streaming system (personal title tracking + stream-while-downloading player).
All code lives at the project root
(`Project_Soul_Anchor/`, mentioned in old READMEs, does not exist).

### Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI + uvicorn | port 8765 |
| BitTorrent | libtorrent 2.0.x | sparse files, piece download |
| Database | PostgreSQL 18, psycopg3 + pool | DSN from `XSTREAM_PG_DSN` (`/etc/xstream.env`); DBs `xstream` + `xstream_test` |
| Frontend | Nuxt 3.16+ / Vue 3.5+, TS + Tailwind + Pinia | port 3000, `@vite-pwa/nuxt` |
| Scraping | httpx + Playwright + selectolax | `scrapers/v2/` pipeline; Playwright only as fallback |
| Deploy | systemd + Caddy | Caddy auto-provisions Let's Encrypt TLS |
| Package Mgmt | `uv` (Python) / `npm` (Node) | |

> **libtorrent is NOT in `pyproject.toml`/`uv.lock`.** After `uv sync` on a
> fresh machine: `uv pip install libtorrent`.

---

## 2. Load-Bearing Rules

Do not violate these; each is locked by regression tests. Rationale and full
detail: `docs/design/architecture-decisions.md`.

- **Disk is the single source of truth** for piece state, never libtorrent's in-memory view.
- **On-demand download discipline (不看不下、看哪下哪)**: add = paused + all-zero priorities; play/seek raises priorities only inside the window; `set_sequential_download(True)` is never allowed.
- **`close_redundant_connections=False`** — libtorrent's default peer-drop on finished↔downloading flips kills streaming.
- **Bootstrap-first verification + fast-resume snapshots** — never blindly recheck finished torrents.
- **All DB access via the psycopg pool** (`core/db/connection.py`); all writes via `core/db/write_queue.py`. Schema and access rules: `docs/design/database.md`.
- **Auth on every API router** except `/api/auth` and `/api/test` (`Depends(require_auth)`).
- **SSE push, no frontend polling timers** (`core/events.py` + `useEventSource.ts`).
- **Multi-star/VR titles and no-magnet titles never enter the DB** (filtered at collection, skipped at sink).
- **Frontend images keep natural aspect ratio** — never `object-cover` (see `docs/design/ui-design.md`).

---

## 3. Core Components

| Component | File | Responsibility |
|---|---|---|
| `TorrentEngine` | `backend/services/torrent_engine.py` | libtorrent session, cache mgmt, tiered eviction, moov scan, orphan GC |
| `PieceStateTracker` | `backend/services/piece_tracker.py` | 3×int bitmap piece state machine |
| `video_stream` | `backend/services/video_stream.py` | Range streaming, hole detection, seek priority |
| Routers | `backend/routers/` | `stream.py` (`/stream`, `/api/check`), `torrents.py`, `stars.py`, `cache.py`, `sync.py` (in-process sync + 6h scheduler), `track.py` (behavior 埋点), `auth.py`, `log.py`, `events.py` (SSE), `magnets.py` (liveness check + purge-dead), `search.py`, `test_helper.py` (debug, no auth) |
| `search_router` | `backend/routers/search.py` | `/api/search?q=` — ijavtorrent + sukebei fallback, sync-consistent filtering; items carry `magnets[]` sorted by `TitleSyncSink._score_magnet` (reuse it, never re-rank by hand), `in_library` + `followed` enrichment; follow reuses `POST /api/stars/add` |
| `EventBus` | `core/events.py` | In-process pub/sub for SSE |
| `MagnetChecker` | `backend/services/magnet_checker.py` | Dead-magnet detection, auto-swap (see `docs/design/magnet-check.md`) |
| Scraper pipeline | `scrapers/v2/` | `tasks/sync_titles.py`: sources → fetchers → extractors → sinks (see `docs/design/diff-sync-design.md`) |

Track events (`/api/track`): `star_view`, `play`, `play_ready`, `play_timeout`,
`play_error`, `play_watch`, `copy_magnet` (meta.source: hero/thumbnail/search),
`like`/`unlike`, `add_star` (meta.source: home/search), `delete_star`, `search`.

---

## 4. Directory Map

```
backend/      FastAPI: main.py, routers/, services/, models/, bench/, regression/
core/         logger.py, log_viewer.py, events.py, db/ (connection, schema, crud, write_queue)
frontend/     Nuxt: pages/, components/{cache,star,ui,video}/, composables/,
              middleware/auth.global.ts, types/api.ts, assets/css/main.css
scrapers/v2/  cli, pipeline, schemas, sources, fetchers, extractors, sinks,
              filters.py, cover_utils, tasks/sync_titles.py
tests/        pytest regression suite + fixtures/ (local BT seed)
scripts/      ops scripts (run.sh, export_covers.py, check_magnets.py, cleanup_*)
deploy/       systemd units (xstream-backend / xstream-frontend)
docs/         design/ ops/ analysis/ skill/ — index: docs/README.md
config.json   actor list (personal, git-ignored, never commit)
```

Root-level oddities: `package.json` is vestigial (frontend deps live in
`frontend/package.json`); `_test_ddg.py` is an ad-hoc smoke script, not tests.

---

## 5. Build & Run

```bash
# Install (PostgreSQL 18 + role xstream + DBs xstream/xstream_test are prerequisites)
uv sync && uv pip install libtorrent
cd frontend && npm install

# Dev: one-click (backend :8765 --reload + Nuxt dev :3000 HMR)
./scripts/run.sh

# Production: build, then restart both services
cd frontend && npm run build
systemctl restart xstream-backend xstream-frontend
```

Caddy / ports / logging / ops commands: `docs/ops/runbook.md`.

**Restart both services after any code change** — uvicorn has no hot reload in
production; the frontend needs `npm run build` + restart.

---

## 6. Code Style

- **Every Python file starts with** `from __future__ import annotations`; 3.11+ annotation syntax (`str | None`).
- Names in English; **comments and docstrings in English**; f-strings.
- SQL: **never** concatenate user input; `%s` placeholders; column identifiers whitelist-validated; JSONB via `psycopg.types.json.Jsonb`.
- Match the surrounding file's conventions; no unsolicited comments/docs files.
- **Git**: one change, one commit; commit right after verification and `git push` (remote is the backup); update docs in the same commit as the code. Format:
  ```
  <type>: <short subject>  (<= 50 chars)

  <long details>  (why + what, wrap at 72 chars)
  ```

---

## 7. Tests

```bash
uv run python -m pytest tests/ -v            # all
uv run python -m pytest tests/<file> -v      # single
```

- Tests auto-skip when real cache files, local seeds, or `xstream_test` are unavailable — they do not fail.
- DB-backed tests need `XSTREAM_PG_DSN` in the environment (the `pg_test_db` fixture repoints it at `xstream_test`; load it via `set -a; . /etc/xstream.env; set +a`).
- Each test file's docstring says what it locks; `tests/conftest.py` holds the shared fixtures (`local_seed`, `real_video_engine`, `pg_test_db`).

---

## 8. Security

- DB password lives in `/etc/xstream.env` (chmod 600, outside repo) — **never commit it or copy it into the repo**. Never commit database files or `config.json`.
- Cover images may contain private content; do not leak them in shared contexts.
- `/cache` is intentionally **not** mounted as static files (prevents direct video download).
- CORS origins from `CORS_ORIGINS` (default `localhost:3000`); `*` with credentials unsupported.
- Login password rotates daily: `rn{YYMMDD}{day % 2}` (UTC). Cookie `xstream_auth=ok`; `SECURE_COOKIES=1` in production.

---

## 9. Documentation Index

Full index with summaries: `docs/README.md`. Most-used:

| Topic | Path |
|---|---|
| Architecture decisions (rationale for §2) | `docs/design/architecture-decisions.md` |
| System architecture / playback flow | `docs/design/architecture.md` |
| Database schema & access rules | `docs/design/database.md` |
| Diff-Sync incremental sync | `docs/design/diff-sync-design.md` |
| Magnet liveness check | `docs/design/magnet-check.md` |
| SSE push architecture | `docs/design/sse-push-architecture.md` |
| UI design spec (apple-design) | `docs/design/ui-design.md` |
| Ops runbook (Caddy, ports, logs, commands) | `docs/ops/runbook.md` |
| Process lifecycle (systemd) | `docs/ops/process-lifecycle.md` |
| HTTPS setup | `docs/ops/https-setup.md` |
| Post-mortems & RCAs | `docs/analysis/` |

Archive convention: new decisions → `docs/design/`, RCAs → `docs/analysis/`,
ops changes → `docs/ops/`, lessons → `docs/skill/`.

---

## 10. Memory & Handoff (nmem)

**nmem (Nowledge Mem MCP) is the sole memory system** — project knowledge lives there across sessions.

- **Before starting work**: query nmem first — `read_working_memory` for the daily briefing, `memory_search` for prior decisions and lessons.
- **After producing durable knowledge** (root causes, design decisions, verified procedures): `memory_add` with the proper `unit_type`, and keep AGENTS.md + `docs/` in sync in the same change.
- **nmem is a knowledge base, not a running log.** One-line conclusion up front, then why + evidence + reusable rule. Supersede the existing memory on the topic (`evolves_from_id` / `memory_supersede`) instead of appending diary entries. Use ASCII diagrams for pipelines/state machines.

---

## 11. Red Lines

- Never leak private data (covers, database content, user memories)
- Never run destructive commands; `trash` > `rm`
- When uncertain, ask the user first
