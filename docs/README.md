# claw-stream Workspace

> 个人作品追踪 + BitTorrent 边下边播播放器

## 项目组成

| 项目 | 路径 | 说明 |
|------|------|------|
| **Star Archive** | `toolbox/star-archive/` | BitTorrent 本地视频流式播放 |
| **Soul Anchor** | `Project_Soul_Anchor/` | DuckDB 本地分层记忆系统 |

---

## Star Archive

基于 BitTorrent 的本地视频播放器，支持 HTTP Range 请求流式播放。

**技术栈**
- 后端：Python 3.11 + FastAPI + libtorrent 2.0.11
- 前端：Nuxt 3 + Vue 3 + Tailwind CSS
- 数据库：DuckDB（封面与元数据）
- 部署：systemd + Caddy 反向代理

**核心特性**
- 按需下载：只下载 head+tail+播放窗口
- 分级缓存：L1 hot / L2 warm / L3 seed / L4 fragment
- Bootstrap-first：finished torrent 秒级验证，跳过分钟级 recheck
- PieceStateTracker：3×int 位图状态机，O(1) POPCNT 查询
- Safari 兼容：checking_files 期间 503 保护

**文档索引**

| 文档 | 内容 |
|------|------|
| [architecture.md](star-archive/architecture.md) | 系统架构、播放流程、状态机 |
| [tiered-cache.md](star-archive/tiered-cache.md) | 四级缓存策略 |
| [bootstrap-first.md](star-archive/bootstrap-first.md) | 跳过 recheck 的验证机制 |
| [piece-tracker.md](star-archive/piece-tracker.md) | 位图状态机架构 |
| [process-lifecycle.md](star-archive/process-lifecycle.md) | systemd 服务配置与运维 |
| [https-setup.md](star-archive/https-setup.md) | Caddy + TLS 配置 |
| [safari-code4.md](star-archive/safari-code4.md) | Safari 兼容性分析 |
| [ui-design.md](star-archive/ui-design.md) | 设计规范 |
| [timeout-debug.md](star-archive/timeout-debug.md) | Caddy 502 排查案例 |
| [tracing-logging.md](star-archive/tracing-logging.md) | 日志体系与排查 |

---

## Project Soul Anchor

基于 DuckDB 的本地分层记忆系统，为智能体提供 L1/L2/L3 三层记忆存储与 Agentic 自主管理能力。

见 `Project_Soul_Anchor/README.md`。

---

## Workspace 指南

- [AGENTS.md](AGENTS.md) — AI 编码助手工作指南
- [MEMORY.md](MEMORY.md) — 长期记忆
- [HEARTBEAT.md](HEARTBEAT.md) — 心跳检查清单
- [SOUL.md](SOUL.md) / [IDENTITY.md](IDENTITY.md) / [USER.md](USER.md) / [TOOLS.md](TOOLS.md)
