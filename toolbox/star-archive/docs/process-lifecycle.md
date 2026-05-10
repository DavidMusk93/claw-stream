# 进程生命周期管理 — Star Archive

## 1. 服务架构

Star Archive 前后端均为 **systemd 托管的长期运行服务**，通过 Caddy 反向代理对外暴露 HTTPS。

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

| 服务 | 端口 | 进程 | systemd unit | 说明 |
|------|------|------|--------------|------|
| Frontend | 3000 | `node .output/server/index.mjs` | `star-archive-frontend.service` | Nuxt 3 SSR 生产构建 |
| Backend | 8765 | `python -m uvicorn backend.main:app` | `star-archive-backend.service` | FastAPI + libtorrent |
| Caddy | 443 | `caddy` | `caddy-claw.service` | HTTPS 反向代理 |

## 2. 修改代码后的重启规则

### Frontend

**任何 `frontend/` 目录下的源码修改（Vue/TS/CSS）都必须重新构建并重启服务。**

```bash
cd toolbox/star-archive/frontend
npm run build
systemctl restart star-archive-frontend
```

> Nuxt 3 生产模式运行的是 `.output/server/index.mjs`，代码热更新不生效。

### Backend

**任何 `backend/**/*.py` 修改后必须重启后端服务。**

```bash
systemctl restart star-archive-backend
```

### Caddy

**`Caddyfile` 或 TLS 配置变更后需要重载或重启。**

```bash
systemctl reload caddy-claw
# 或
systemctl restart caddy-claw
```

## 3. systemd 服务配置

### star-archive-backend.service

```ini
[Unit]
Description=Star Archive Backend (FastAPI + BitTorrent)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/.openclaw/workspace/toolbox/star-archive
Environment=PYTHONPATH=/root/.openclaw/workspace/toolbox/star-archive
ExecStart=/root/.openclaw/workspace/toolbox/star-archive/.venv/bin/python \
          -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --log-level info
Restart=always
RestartSec=5s
User=root

[Install]
WantedBy=multi-user.target
```

### star-archive-frontend.service

```ini
[Unit]
Description=Star Archive Frontend (Nuxt production)
After=network.target star-archive-backend.service
Wants=star-archive-backend.service

[Service]
Type=simple
WorkingDirectory=/root/.openclaw/workspace/toolbox/star-archive/frontend
Environment=NITRO_HOST=0.0.0.0
Environment=NITRO_PORT=3000
ExecStart=/usr/bin/node .output/server/index.mjs
Restart=always
RestartSec=5s
User=root

[Install]
WantedBy=multi-user.target
```

## 4. 常用运维命令

```bash
# 查看状态
systemctl status star-archive-backend
systemctl status star-archive-frontend

# 查看日志
journalctl -u star-archive-backend -f
journalctl -u star-archive-frontend -f

# 重启
systemctl restart star-archive-backend
systemctl restart star-archive-frontend

# 查看端口占用
ss -tlnp | grep -E '3000|8765|443'
```

## 5. 启动顺序依赖

```
network.target
    └─ star-archive-backend.service
         └─ star-archive-frontend.service (After + Wants)
              └─ caddy-claw.service (反向代理到 3000/8765)
```

- Frontend 依赖 Backend（`Wants`）：若后端未启动，前端仍能启动，但 API 调用会失败。
- Caddy 独立运行：配置中 `reverse_proxy localhost:3000` 和 `reverse_proxy localhost:8765`，无需 systemd 层面的 `After` 依赖。

## 6. 日志文件位置

| 日志 | 路径 | 说明 |
|------|------|------|
| 后端访问日志 | `logs/backend-access.log` | AccessLogMiddleware 输出 |
| Torrent 引擎 | `logs/torrent-engine.log` | libtorrent 状态与 alert |
| 视频流 | `logs/video-stream.log` | Range 请求与 hole 检测 |
| Piece 追踪 | `logs/piece-tracker.log` | PieceStateTracker 状态变化 |
| 前端 | `logs/frontend.log` | Nuxt 运行时日志 |
| systemd | `journalctl` | 进程启动/崩溃/重启记录 |

## 7. 故障排查速查

| 现象 | 排查步骤 |
|------|----------|
| 前端 502/404 | `systemctl status star-archive-frontend` → 检查 `npm run build` 是否执行 |
| API 无响应 | `systemctl status star-archive-backend` → 检查 `logs/torrent-engine.log` |
| 视频无法播放 | 检查 `logs/video-stream.log` 的 hole timeout 和 `state=finished` 记录 |
| 缓存显示 100% 但实际未完成 | 见 `ui-design.md` 与 `logs/torrent-engine.log` 的 progress 修正逻辑 |
| HTTPS 证书错误 | `systemctl status caddy-claw` → 检查 `/var/log/caddy/` |
