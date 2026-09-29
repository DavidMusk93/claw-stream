# Process Lifecycle Management

---

## Table of Contents

- [Service Architecture](#service-architecture)
- [Restart Rules After Code Changes](#restart-rules-after-code-changes)
  - [Frontend](#frontend)
  - [Backend](#backend)
  - [Caddy](#caddy)
- [systemd Service Configuration](#systemd-service-configuration)
  - [xstream-backend.service](#xstream-backendservice)
  - [xstream-frontend.service](#xstream-frontendservice)
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
| Frontend | 3000 | `node .output/server/index.mjs` | `xstream-frontend.service` | Nuxt 3 SSR production build |
| Backend | 8765 | `uvicorn backend.main:app` | `xstream-backend.service` | FastAPI + libtorrent |
| Caddy | 443 | `caddy` | `caddy.service` | HTTPS reverse proxy |

---

## Restart Rules After Code Changes

### Frontend

**Any source change under `frontend/` (Vue / TS / CSS) requires a rebuild and service restart.**

```bash
cd /root/xstream/frontend
npm run build
systemctl restart xstream-frontend
```

> Nuxt 3 production mode runs `.output/server/index.mjs`; hot reload does not apply.

### Backend

**Any change under `backend/**/*.py` requires a backend service restart.**

```bash
systemctl restart xstream-backend
```

> Do not manually `pkill` + `nohup &`. All processes are managed by systemd.

### Caddy

**Changes to `Caddyfile` or TLS configuration require a reload or restart.**

```bash
systemctl reload caddy
# or
systemctl restart caddy
```

---

## systemd Service Configuration

### xstream-backend.service

```ini
[Unit]
Description=xstream Backend (FastAPI + BitTorrent)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/xstream
Environment=PYTHONPATH=/root/xstream
ExecStart=/root/xstream/.venv/bin/python \
          -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --log-level info
Restart=on-failure
RestartSec=5s
User=root

[Install]
WantedBy=multi-user.target
```

### xstream-frontend.service

```ini
[Unit]
Description=xstream Frontend (Nuxt production)
After=network.target xstream-backend.service
Wants=xstream-backend.service

[Service]
Type=simple
WorkingDirectory=/root/xstream/frontend
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
systemctl status xstream-backend
systemctl status xstream-frontend

# View logs
journalctl -u xstream-backend -f
journalctl -u xstream-frontend -f

# Restart
systemctl restart xstream-backend
systemctl restart xstream-frontend

# Check port usage
ss -tlnp | grep -E '3000|8765|443'
```

---

## Startup Order Dependencies

```
network.target
    └─ xstream-backend.service
         └─ xstream-frontend.service (After + Wants)
              └─ caddy.service (reverse proxy to 3000/8765)
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
