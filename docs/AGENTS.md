<!-- From: /root/.openclaw/workspace/docs/AGENTS.md -->
<!-- 管辖范围：整个 workspace 目录树 -->
# AGENTS.md — Workspace & Star Archive 指南

本文件是 AI 编码助手的工作指南。阅读者应被视为对该仓库一无所知。

---

## 1. 项目概览

本仓库包含两个主要部分：

1. **Project Soul Anchor**（`Project_Soul_Anchor/`）—— 基于 DuckDB 的本地分层记忆系统，为智能体提供 L1/L2/L3 三层记忆存储与 Agentic 自主管理能力。
2. **Toolbox**（`toolbox/`）—— 小型独立工具集，当前仅包含 `star-archive` 子项目。

### 1.1 Star Archive 架构

**Star Archive** 是基于 BitTorrent 的本地视频流式播放系统：

- **后端**：Python 3.11 + FastAPI + libtorrent 2.0.11
- **前端**：Nuxt 3 + Vue 3
- **数据库**：DuckDB
- **部署**：systemd + Caddy 反向代理

**核心组件：**

| 组件 | 文件 | 职责 |
|---|---|---|
| `TorrentEngine` | `backend/services/torrent_engine.py` | libtorrent 会话、生命周期、缓存管理 |
| `PieceStateTracker` | `backend/services/piece_tracker.py` | 3×int bitmap 位图管理 piece 状态 |
| `video_stream` | `backend/services/video_stream.py` | Range 请求流式读取、hole 检测 |
| `stream_router` | `backend/routers/stream.py` | `/stream/`、`/api/check/` 端点 |
| `torrents_router` | `backend/routers/torrents.py` | `/torrent/add/`、`/torrent/status/` 端点 |

**关键架构决策：**

- **Sparse File + SEEK_DATA/SEEK_HOLE**：Linux 稀疏文件存储，未下载区域不占磁盘
- **Bootstrap-first verification**：finished torrent 先 lseek 扫描，数据完整则跳过 hash recheck
- **Tiered cache**：L1 hot / L2 warm / L3 seed / L4 fragment，评分替代纯 LRU
- **PieceStateTracker**：独立 piece 状态机，libtorrent `have_piece()` 在 checking 时不可靠

### 1.2 Project Soul Anchor 架构

见 `Project_Soul_Anchor/README.md` 及 `Project_Soul_Anchor_Research.md`。

---

## 2. 构建与运行

### 2.1 Star Archive

```bash
cd toolbox/star-archive
python3.11 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt  # duckdb, fastapi, uvicorn, libtorrent
```

**systemd 启动（生产环境）：**
```bash
systemctl restart star-archive-backend
systemctl restart star-archive-frontend
```

**本地开发启动：**
```bash
# 后端
PYTHONPATH=/root/.openclaw/workspace/toolbox/star-archive \
  .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765

# 前端
cd frontend && npm run build && node .output/server/index.mjs
```

### 2.2 Project Soul Anchor

```bash
cd Project_Soul_Anchor
python3.11 -m venv .venv
./.venv/bin/python -m pip install duckdb ruff
./.venv/bin/python -m pip install -e .
```

---

## 3. 代码风格指南

### 3.1 通用约定

- **每文件开头必须包含** `from __future__ import annotations`
- 使用类型注解（Python 3.11+ 语法，如 `str | None`）
- 类名、函数名、变量名使用英文；**注释和 docstring 使用中文**
- 字符串格式化：优先使用 f-string；SQL 中参数绑定优先使用 `?` 占位符
- 对 VARIANT 列的 Python dict 值，使用 `variant_sql_literal()` 生成 SQL 表达式嵌入查询（避免驱动层参数绑定行为不一致）

### 3.2 SQL 安全

- 禁止将用户输入直接拼接进 SQL。列名等标识符必须通过白名单校验
- 值参数使用 `?` 占位符绑定；仅 VARIANT 类型的复杂 dict 允许使用 `variant_sql_literal()` 生成的表达式

### 3.3 Git 提交规范

- **一个改动一个 commit**：每处 bug 修复、每个功能、每次重构都单独提交
- **commit message 格式**：
  ```
  <type>: <short subject>  (<= 50 chars)

  <long details>  (why + what, 换行 72 chars)
  ```
- **不要攒改动**：修复完立即提交
- **文档随代码一起提交**：改了代码同时更新文档，放在同一个 commit 中

---

## 4. 测试命令

### Star Archive

```bash
cd toolbox/star-archive/tests
../.venv/bin/python -m pytest test_piece_tracker_regression.py -v
../.venv/bin/python -m pytest test_torrent_engine_arch.py -v
../.venv/bin/python -m pytest test_stream_regression.py -v  # 需要真实缓存文件
```

### Project Soul Anchor

```bash
cd Project_Soul_Anchor
./.venv/bin/python -m unittest -v
./.venv/bin/python -m unittest tests.test_schema -v
./.venv/bin/python -m unittest tests.test_agentic_audit -v
```

---

## 5. 安全与运维

### 5.1 数据安全

- DuckDB 是**单文件数据库**（`.duckdb`），默认已在 `.gitignore` 中排除，**切勿将数据库文件提交到 git**
- 记忆内容可能包含用户隐私信息；agent 在共享上下文中不得泄露记忆内容

### 5.2 服务重启规则

**修改 `backend/**/*.py` 后必须重启服务。**

```bash
systemctl restart star-archive-backend
```

`backend/main.py` 通过 uvicorn 运行，代码热更新不会生效。

**Caddy 反向代理配置变更：**
```bash
systemctl reload caddy-claw
```

### 5.3 端口占用

| 端口 | 服务 | 说明 |
|------|------|------|
| 80 | Caddy | Let's Encrypt HTTP-01 验证 |
| 443 | Caddy | HTTPS 反向代理 |
| 3000 | Nuxt frontend | SSR 渲染 |
| 8765 | FastAPI backend | BitTorrent + HTTP API |
| 8444 | s-ui | 代理服务 |

### 5.4 L3 核心契约变更流程（Soul Anchor）

L3 (`core_contract`) 的更新必须极度谨慎：
1. 如需修改，先对目标知识行打快照（`KnowledgeVersioning.create_snapshot`）
2. 执行变更
3. 如效果不符合预期，使用 `rollback_to_snapshot` 回滚
4. 永远不应通过 Agentic Loop 自动写入 L3

---

## 6. 记忆系统（Workspace 级别）

### 6.1 每日笔记

- `memory/YYYY-MM-DD.md` — 原始日志
- `memory/.dreams/` — 梦境/事件流数据

### 6.2 长期记忆

- `MEMORY.md` — 精选长期记忆（仅在主会话中加载）
- 不要在没有用户请求时向共享上下文泄露 `MEMORY.md` 内容

### 6.3 重要原则

- **记忆有限**：想记住的事必须写入文件，不要依赖"脑内笔记"
- **Text > Brain** 📝
- 学到教训 → 更新 `AGENTS.md` 或相关技能文件
- 犯错 → 记录以防未来重蹈覆辙

---

## 7. 红线（Red Lines）

- 绝不外泄隐私数据
- 不运行破坏性命令；`trash` > `rm`
- 不确定时，先问用户

---

## 8. 外部 vs 内部

**可自由执行：**
- 读取文件、探索、整理、学习
- 搜索网页、查看日历
- 在 workspace 内工作

**需先询问：**
- 发送邮件、推文、公开帖子
- 任何会离开本机的操作

---

## 9. 心跳（Heartbeats）

收到心跳轮询时，不要只回复 `HEARTBEAT_OK`。可执行的有效工作：
- 读取整理 memory 文件
- 检查项目 git status
- 更新文档
- 审查并更新 `MEMORY.md`

**何时保持安静：**
- 23:00–08:00（除非紧急）
- 用户明显忙碌
- 距上次检查 < 30 分钟且无新变化

---

## 10. 文档索引

| 文档 | 路径 | 内容 |
|------|------|------|
| 项目总览 | `docs/README.md` | Workspace 整体介绍 |
| 进程生命周期 | `docs/star-archive/process-lifecycle.md` | systemd 服务配置与运维 |
| 系统架构 | `docs/star-archive/architecture.md` | 播放流程、状态机、组件交互 |
| 分级缓存 | `docs/star-archive/tiered-cache.md` | L1/L2/L3/L4 缓存策略 |
| Bootstrap-first | `docs/star-archive/bootstrap-first.md` | 跳过 recheck 的验证机制 |
| Piece Tracker | `docs/star-archive/piece-tracker.md` | 位图状态机架构 |
| HTTPS 配置 | `docs/star-archive/https-setup.md` | Caddy + TLS |
| Safari 兼容 | `docs/star-archive/safari-code4.md` | code=4 根因分析 |
| UI 设计 | `docs/star-archive/ui-design.md` | 设计规范 |
| 性能优化 | `docs/star-archive/piece-tracker-optimization.md` | 位运算优化记录 |
| 超时调试 | `docs/star-archive/timeout-debug.md` | Caddy 502 排查案例 |
| 日志体系 | `docs/star-archive/tracing-logging.md` | 日志文件与排查 |
| Soul Anchor | `Project_Soul_Anchor/README.md` | 记忆系统手册 |
