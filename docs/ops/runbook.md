# Operations Runbook

Production operations: Caddy, ports, logging, data refresh, common commands.
For systemd service config see `docs/ops/process-lifecycle.md`; for HTTPS/TLS
architecture see `docs/ops/https-setup.md`.

## Caddy Reverse Proxy

Caddy serves `cc.guohuasun.com` on 443, auto-provisions Let's Encrypt certificates:

- `/images/*` → served directly from disk bind mount `/var/lib/caddy/xstream-images`
  (requires `mount --bind /root/xstream/images /var/lib/caddy/xstream-images`),
  with `Cache-Control: public, max-age=604800, immutable`
- `/api/*`, `/stream/*`, `/torrent/*`, `/cache/*` → `localhost:8765`
- Everything else → `localhost:3000` (Nuxt SSR, 10s dial / 30s response timeout)
- **HTTP/3 (QUIC) is disabled** — mobile networks throttle/drop UDP, causing 50–80s cover loads

The live config is `/etc/caddy/Caddyfile` (unit `caddy.service`); the repo
`Caddyfile` is the source of truth — copy it over, then reload:

```bash
cp /root/xstream/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
```

## Port Allocation

| Port | Service | Notes |
|------|---------|-------|
| 80 | Caddy | HTTP-01 challenge, `/health` |
| 443 | Caddy | HTTPS reverse proxy |
| 3000 | Nuxt frontend | SSR rendering |
| 8765 | FastAPI backend | BitTorrent + HTTP API |

## Logging

- Backend log directory: `/root/xstream/logs/` (override with `LOG_DIR` env var)
- Per-module files named after the logger: `backend.log`, `backend-access.log`, `torrent-engine.log`, `video-stream.log`, `stream-router.log`, `piece-tracker.log`, `db-ops.log`, `db-write-queue.log`, `sync.log`, `events.log`, `events-router.log`…
- 10MB rollover per file, keep 5 backups
- trace_id chain tracking (HTTP header `x-trace-id`; middleware generates one if absent)
- Environment variable `LOG_JSON=1` switches to JSON output
- CLI log query tool: `python3 core/log_viewer.py tail -n 50`

## Data Refresh & Covers

```bash
# Refresh title data (scrapers v2 pipeline → PostgreSQL, then prints stats)
./refresh.sh [config.json]

# Export covers from title_covers blobs to images/titles/{code}/{code}.jpg
# (also generates {code}_thumb.jpg, 400px wide, for list/grid views)
.venv/bin/python scripts/export_covers.py

# Fetch actor covers from DMM/FANZA CDN and base64-encode them
./fetch-covers.sh [config.json]
./b64-encode.sh [image-dir]
```

`/api/stars` returns ready-to-use cover URLs: static
`/images/titles/{code}/{code}.jpg` (plus `cover_thumb_url` for the 400px
variant and `cover_mid_url` for the 800px hero variant) when the files exist
on disk, otherwise `/api/cover/{code}`. The payload only carries fields the
frontend renders (no views/likes/charming_intro/download_url).
`/api/cover/{code}` itself resolves disk static file (307 redirect) →
in-memory LRU → `title_covers` blob (+ disk backfill), so new titles never
404 even if the disk export lagged. `?thumb=1` redirects to the small
`{code}_thumb.jpg` variant when it exists.

## Common Commands

```bash
# View backend logs
journalctl -u xstream-backend -f

# Check torrent status
curl -s http://localhost:8765/torrent/status/<hash> | python3 -m json.tool

# Check sparse file real size
stat --format="logical=%s actual=%b*%B=%B" /root/xstream/cache/torrent/<hash>/.../*.mp4

# View cache metrics
curl -s http://localhost:8765/api/cache/metrics | python3 -m json.tool

# Trigger / inspect magnet liveness check (historical sweep)
.venv/bin/python scripts/check_magnets.py --scope all

# Health check
curl -s http://localhost:8765/api/health

# DB stats
python3 -m core.db stats

# Ad-hoc SQL against the live database
set -a; . /etc/xstream.env; set +a
psql "$XSTREAM_PG_DSN"

# Table bloat: PostgreSQL autovacuum reclaims dead tuples continuously —
# the DuckDB rewrite-on-UPDATE bloat problem is gone. If a table ever does
# bloat (check pg_stat_user_tables n_dead_tup), use:
#   VACUUM (ANALYZE) titles;
# or pg_repack for lock-free shrinkage.
```
