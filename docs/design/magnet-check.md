# Magnet 可用性校验（Magnet Liveness Check）

> **注（2026-09-19）**：存储已从 DuckDB 迁移到 PostgreSQL 18。文中的
> `db_write` 串行写队列仍然保留（API 未变），但 DuckDB 单写者锁的限制
> 已不存在 —— PG 原生支持并发写入。机制与流程不变。

## 问题

Diff-Sync 选"最佳 magnet"（`scrapers/v2/sinks.py:_score_magnet`）纯按元数据打分
（分辨率 + hhd800 + 种子数），从不验证 swarm 是否活着。死 magnet 会在
TorrentEngine 中永远停在 `downloading_metadata`，前端 180s 后才报笼统的加载超时。

## 机制

```
POST /api/magnets/check ──→ MagnetChecker (backend/services/magnet_checker.py)
                                 │  独立 lt.session（与 TorrentEngine 完全隔离）
                                 │  并发 32，每个 magnet：
                                 │  加种(paused + default_dont_download，不写盘)
                                 │  → resume → 轮询 has_metadata
                                 │  ├─ 60s 内拿到 metadata → alive
                                 │  ├─ 超时但 peers>0 → 宽限一次 90s
                                 │  └─ 仍无 → dead → 逐个校验 all_magnets 候选
                                 │        ├─ 第一个活的 → 换主 magnet (swap)
                                 │        └─ 全死 → magnet_status='dead'
                                 ▼
                         db_write 串行写队列 → titles 新列
                                 │
                         SSE: magnets.check_started /
                              magnets.check_progress /
                              magnets.check_completed / magnets.check_error
```

判定标准：**metadata 到达 = alive**。peers>0 但 metadata 迟迟不到给一次宽限
（活 swarm 但 metadata 慢；注意同主机其他 libtorrent session 的共享 DHT 可能
给随机 hash 返回零星 peer，因此 peers 数只是宽限依据，不作为 alive 依据）。

磁盘即真相优化：`cache/torrent/{hash}/{hash}.torrent` 已存在的 title 直接判
alive，不消耗网络校验。

## DB 列（titles）

| 列 | 含义 |
|---|---|
| `magnet_status` | `NULL` 未校验 / `'ok'` / `'dead'` |
| `magnet_checked_at` | 上次校验时间 |
| `magnet_checked_hash` | 上次校验时的主 hash，用于检测 sync 换主 |

## 触发方式

| 入口 | scope | 说明 |
|---|---|---|
| `POST /api/magnets/check` | all/dead/unchecked/changed | 手动，需 auth cookie |
| sync 成功后自动 | changed | `sync.py::_run_check_bg` 尾部触发；运行中自动跳过 |
| `scripts/check_magnets.py` | 默认 changed，`--scope all` 全量 | 薄 HTTP 触发器，自动算每日密码 |

scope 语义（`crud.load_titles_for_magnet_check`）：

- `unchecked`：从未校验，或 sync 换了主 hash（`magnet_hash IS DISTINCT FROM magnet_checked_hash`）
- `dead`：重试已标记 dead 的
- `changed`：unchecked OR dead（sync 后默认）
- `all`：所有有 magnet 的 title（历史全量，约 2265 个，最坏 1-2 小时）

## 已知限制

- sync 的 ON CONFLICT 会用抓取侧新鲜数据覆盖 `magnet`/`all_magnets`，可能
  复活死链 —— 由 sync 后的 `changed` 校验兜底纠正。
- `all_magnets` 内逐候选的死标记不落库（每次 sync 全量覆盖，落库也会丢）；
  只依赖换链后的主 magnet。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAGNET_CHECK_TIMEOUT` | 60 | 单个 magnet metadata 等待秒数 |
| `MAGNET_CHECK_GRACE_TIMEOUT` | 90 | peers>0 时的宽限秒数 |
| `MAGNET_CHECK_CONCURRENCY` | 32 | 并发校验数 |

## 前端

`/api/stars` 透出 `magnet_status`；`dead` 的 title 在 TitleCard/StarCard
置灰 + 「链接失效」角标，禁止播放。

## 清理（purge）

`POST /api/magnets/purge-dead`（或 `scripts/check_magnets.py --purge`）删除
不可播放的 title：`magnet_status='dead'` 或根本没有 magnet 的行。
`user_liked=1` 的永删不掉（单独报告）。**删除前先把 (star_id, code) 以
`reason='dead_magnet'` 封入 `title_blacklist`**，否则下一轮同步会把这些
code 当新作品重新下载封面、重新入库；黑名单 7 天重试窗口仍然有效——
如果 swarm 复活，作品会在重试时自然回归。删除同时清理封面目录
（`images/titles/{code}`）并对孤儿 cache 跑 `gc_orphaned_torrents`。
与进行中的校验互斥（409）。

sink 侧预防：`scrapers/v2/sinks.py:write_batch` 跳过无 magnet 的 item
（`skipped_no_magnet` 计数），源站将来补上 magnet 时该 code 会作为新作品
正常录入。

## 首次全量结果（2026-09-14）

2265 个有 magnet 的历史作品：alive 1885 / swapped 132（死主链自动换活候选）
/ dead 379（全部候选无救）。另有 274 个从未有 magnet 的作品，purge 时一并
删除。
