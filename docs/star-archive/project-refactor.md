# Project Refactor Log

> Date: 2025-06-07
> Principle: Layout should be self-explanatory — understand functionality from directory names alone.

---

## 1. Directory Flattening

### Problem

Code was nested under `toolbox/star-archive/`, creating unnecessary depth:

```
# Before (confusing)
toolbox/star-archive/
├── backend/
├── frontend/
├── core/
└── ...

# After (clear)
backend/
frontend/
core/
scrapers/
tests/
scripts/
deploy/
```

The `toolbox/` layer served no purpose (only one project). The `star-archive/` name was a legacy artifact.

### Migration Steps

1. **Stop services** before moving files to avoid runtime errors.
2. **Move tracked files** first (code, configs, scripts).
3. **Move untracked data directories** (`data/`, `cache/`, `logs/`) — on the same filesystem, `mv` is instantaneous (inode update only).
4. **Update systemd services** — `WorkingDirectory`, `PYTHONPATH`, `ExecStart` paths.
5. **Update `.gitignore`** — merge root and nested ignore rules.
6. **Update docs** — remove all `toolbox/star-archive/` path references.
7. **Restart and verify** — health checks on both backend (8765) and frontend (3000).

### Key Insight

Python files use `os.path.dirname(os.path.abspath(__file__))` for path resolution. After moving files from `toolbox/star-archive/backend/main.py` to `backend/main.py`, `SCRIPT_DIR` automatically resolves to `/root/claw-stream` instead of `/root/claw-stream/toolbox/star-archive`. No code changes needed for relative path calculations.

### Gotchas

- **Absolute paths in scripts**: `scripts/fill_all_covers.py` had `sys.path.insert(0, "/root/claw-stream/toolbox/star-archive")`. Replaced with relative path.
- **Caddyfile log path**: Hardcoded `/root/claw-stream/toolbox/star-archive/logs/...` → `/root/claw-stream/logs/...`.
- **systemd service files in `deploy/`**: Were outdated (`/root/.openclaw/workspace/...`). Updated to match actual production paths (`/root/claw-stream/`).

---

## 2. Terminology Unification

### Decision

Replace all occurrences of "女优" with **"actor"** throughout codebase and docs.

### Rationale

- "女优" is domain-specific Japanese terminology. "Actor" is neutral, internationally understood, and avoids cultural specificity in code.
- API error messages, comments, docstrings, and documentation all updated.

### Files Modified

- `backend/routers/stars.py`, `sync.py`, `torrents.py`
- `core/db/crud.py`
- `scrapers/v2/tasks/sync_titles.py`
- `docs/star-archive/deletion-design.md`, `diff-sync-design.md`
- `AGENTS.md`

---

## 3. English-ification

### Decision

All internal documentation and code comments translated to **English**.

### Rationale

- AI coding assistants (the primary consumers of this codebase) operate best in English.
- English comments reduce token count for context windows.
- Aligns with the "利于开发" (developer-friendly) first principle.

### Scope

- `AGENTS.md` — fully translated
- `docs/README.md` — fully translated
- Code comments in `backend/services/`, `backend/routers/`, `core/` — translated via parallel subagents
- Frontend code (Vue/TS) — Chinese UI labels preserved (user-facing), internal comments translated

### Exception

User-facing strings (HTTP error details, frontend UI labels) remain in Chinese for end-user experience.

---

## 4. Verification Checklist

After any layout or path change:

- [ ] `systemctl restart star-archive-backend` — check `journalctl -u star-archive-backend -f`
- [ ] `systemctl restart star-archive-frontend` — check `curl -s http://localhost:3000`
- [ ] `curl -s http://localhost:8765/api/health` — backend API health
- [ ] `curl -s http://localhost:8765/api/cache/metrics` — cache metrics accessible
- [ ] Verify database path resolves correctly (`data/claw.duckdb`)
- [ ] Verify cache path resolves correctly (`cache/torrent/`)
- [ ] Verify log directory writes to new location (`logs/`)

---

## 5. Current Layout

```
/root/claw-stream/
├── backend/        # FastAPI + libtorrent services
├── core/           # Shared logger, DuckDB, CLI tools
├── frontend/       # Nuxt 3 + Vue 3 SPA
├── scrapers/       # Playwright + HTTP scrapers
├── tests/          # pytest regression suite
├── scripts/        # One-off ops scripts
├── deploy/         # systemd unit files
├── docs/           # Architecture docs (this tree)
├── data/           # DuckDB database (gitignored)
├── cache/          # BitTorrent cache (gitignored)
└── logs/           # Runtime logs (gitignored)
```
