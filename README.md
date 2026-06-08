# claw-stream

> 个人作品追踪 + BitTorrent 边下边播播放器

## Star Archive

基于 BitTorrent 的本地视频流式播放系统。

**技术栈**：Python 3.11 + FastAPI + libtorrent 2.0.x / Nuxt 3 + Vue 3 / DuckDB / systemd + Caddy

**核心特性**：按需下载 · 分级缓存 · Bootstrap-first 验证 · PieceStateTracker · Safari 兼容

## 文档

全部文档集中在 [`docs/`](docs/) 目录：

- [docs/README.md](docs/README.md) — Star Archive 文档索引
- [AGENTS.md](AGENTS.md) — AI 编码助手工作指南

## 快速启动

```bash
# 后端
systemctl restart star-archive-backend

# 前端
systemctl restart star-archive-frontend
```
