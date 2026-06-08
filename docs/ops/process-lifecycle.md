# Process Lifecycle Management

---

## Table of Contents

- [Service Architecture](#service-architecture)
- [Restart Rules After Code Changes](#restart-rules-after-code-changes)
  - [Frontend](#frontend)
  - [Backend](#backend)
  - [Caddy](#caddy)
- [systemd Service Configuration](#systemd-service-configuration)
  - [star-archive-backend.service](#star-archive-backendservice)
  - [star-archive-frontend.service](#star-archive-frontendservice)
- [Common Operations Commands](#common-operations-commands)
- [Startup Order Dependencies](#startup-order-dependencies)
- [Log File Locations](#log-file-locations)

---

## Service Architecture

Both frontend and backend are **long-running services managed by systemd**, exposed via Caddy reverse proxy over HTTPS.

```
┌─────────────┐     ┌─────────────────────────────┐     ┌──────────────┐
│   Client    │────▶│  Caddy (HTTPS :443)         │────▶│  Frontend    │
│  (Browser)  │     │  reverse proxy              │     │  (:3000)     │
└─────────────┘     │                             │     └──────────────┘
                    │  /api/* /stream/* /torrent/*│────────▶┌──────────────┐
                    │  ─────────────────────────▶ │         │  Backend     │
                    └─────────────────────────────┘         │  (:8765)     │
                                                            └──────────────┘
```

| Service | Port | Process | systemd Unit | Description |
|---------|------|---------|--------------|-------------|
| Frontend | 3000 | `node .output/server/index.mjs` | `star-archive-frontend.service` | Nuxt 3 SSR production build |
| Backend | 8765 | `uvicorn backend.main:app` | `star-archive-backend.service` | FastAPI + libtorrent |
| Caddy | 443 | `caddy` | `caddy-claw.service` | HTTPS reverse proxy |

---

## Restart Rules After Code Changes

### Frontend

**Any source change under `frontend/` (Vue / TS / CSS) requires a rebuild and service restart.**

```bash
cd /root/claw-stream/frontend
npm run build
systemctl restart star-archive-frontend
```

> Nuxt 3 production mode runs `.output/server/index.mjs`; hot reload does not apply.

### Backend

**Any change under `backend/**/*.py` requires a backend service restart.**

```bash
systemctl restart star-archive-backend
```

> Do not manually `pkill` + `nohup &`. All processes are managed by systemd.

### Caddy

**Changes to `Caddyfile` or TLS configuration require a reload or restart.**

```bash
systemctl reload caddy-claw
# or
systemctl restart caddy-claw
```

---

## systemd Service Configuration

### star-archive-backend.service

```ini
[Unit]
Description=claw-stream Backend (FastAPI + BitTorrent)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/claw-stream
Environment=PYTHONPATH=/root/claw-stream
ExecStart=/root/claw-stream/.venv/bin/python \
          -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --log-level info
Restart=on-failure
RestartSec=5s
User=root

[Install]
WantedBy=multi-user.target
```

### star-archive-frontend.service

```ini
[Unit]
Description=claw-stream Frontend (Nuxt production)
After=network.target star-archive-backend.service
Wants=star-archive-backend.service

[Service]
Type=simple
WorkingDirectory=/root/claw-stream/frontend
Environment=NITRO_HOST=0.0.0.0
Environment=NITRO_PORT=3000
ExecStart=/usr/bin/node .output/server/index.mjs
Restart=always
RestartSec=5s
User=root

[Install]
WantedBy=multi-user.target
```

---

## Common Operations Commands

```bash
# View status
systemctl status star-archive-backend
systemctl status star-archive-frontend

# View logs
journalctl -u star-archive-backend -f
journalctl -u star-archive-frontend -f

# Restart
systemctl restart star-archive-backend
systemctl restart star-archive-frontend

# Check port usage
ss -tlnp | grep -E '3000|8765|443'
```

---

## Startup Order Dependencies

```
network.target
    └─ star-archive-backend.service
         └─ star-archive-frontend.service (After + Wants)
              └─ caddy-claw.service (reverse proxy to 3000/8765)
```

---

## Log File Locations

| Log | Path | Description |
|-----|------|-------------|
| Backend access log | `logs/backend-access.log` | AccessLogMiddleware output |
| Torrent engine | `logs/torrent-engine.log` | libtorrent status and alerts |
| Video stream | `logs/video-stream.log` | Range requests and hole detection |
| Piece tracker | `logs/piece-tracker.log` | PieceStateTracker state changes |
| Frontend | `logs/frontend.log` | Nuxt runtime logs |
| systemd | `journalctl` | Process start / crash / restart records |

See [`tracing-logging.md`](tracing-logging.md) for log query examples and troubleshooting.
