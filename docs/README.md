# Star Archive 文档中心

> 全部文档集中在 `docs/star-archive/` 目录。

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [architecture.md](star-archive/architecture.md) | 系统架构、播放流程、状态机、组件交互、故障排查速查 |
| [cache-architecture.md](star-archive/cache-architecture.md) | 缓存模块架构（第一性原理：流畅播放）、生命周期、淘汰策略、最佳实践 |
| [tiered-cache.md](star-archive/tiered-cache.md) | 四级缓存策略与评分公式 |
| [bootstrap-first.md](star-archive/bootstrap-first.md) | 跳过 recheck 的验证机制 |
| [piece-tracker.md](star-archive/piece-tracker.md) | 位图状态机架构 |
| [process-lifecycle.md](star-archive/process-lifecycle.md) | systemd 服务配置与运维 |
| [https-setup.md](star-archive/https-setup.md) | Caddy + TLS 配置 |
| [safari-code4.md](star-archive/safari-code4.md) | Safari 兼容性分析 |
| [ui-design.md](star-archive/ui-design.md) | 前端设计规范 |
| [piece-tracker-optimization.md](star-archive/piece-tracker-optimization.md) | 位运算优化记录 |
| [timeout-debug.md](star-archive/timeout-debug.md) | Caddy 502 排查案例 |
| [tracing-logging.md](star-archive/tracing-logging.md) | 日志体系与排查方法 |
| [deletion-design.md](star-archive/deletion-design.md) | 删除 star 的安全流程设计 |
| [diff-sync-design.md](star-archive/diff-sync-design.md) | Diff-Sync 增量同步算法设计 |

---

## 入口指南

- **[AGENTS.md](../AGENTS.md)** — AI 编码助手工作指南（构建、运行、代码风格、测试、运维）
- **[README.md](../README.md)** — 项目总览与快速启动
