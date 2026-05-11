# claw-stream

> 个人作品追踪 + BitTorrent 边下边播播放器

## 项目组成

- **Star Archive** (`toolbox/star-archive/`) — BitTorrent 本地视频流式播放
- **Soul Anchor** (`Project_Soul_Anchor/`) — DuckDB 本地分层记忆系统

## 文档

全部文档集中在 [`docs/`](docs/) 目录。

- [docs/README.md](docs/README.md) — 项目总览
- [docs/AGENTS.md](docs/AGENTS.md) — AI 编码助手工作指南
- [docs/star-archive/architecture.md](docs/star-archive/architecture.md) — Star Archive 系统架构

## 快速启动

```bash
# Star Archive 后端
systemctl restart star-archive-backend

# Star Archive 前端
systemctl restart star-archive-frontend
```
