# 女优删除设计 —— 安全稳健的数据与缓存清理流程

## 1. 问题背景

删除一个女优（star）会触发多个数据层面的变更：

| 层面 | 数据 | 位置 |
|---|---|---|
| 配置 | `config.json` 中的 star 条目 | 文件系统 |
| 数据库 | `stars` / `titles` / `social_posts` | DuckDB |
| 缓存 | 已下载的 torrent 文件（视频 + `.torrent`） | `cache/torrent/<hash>/` |
| 内存 | `TorrentEngine.torrents` 中的 handle / tracker | 进程内存 |

**历史 bug**：`delete_star` 端点先调用 `db.delete_star_by_code()` 删除数据库记录，再查询 `titles.magnet_hash` 以清理缓存。由于记录已被删除，`SELECT ... WHERE star_code = ?` 永远返回空，导致 torrent 文件残留在磁盘上，成为**孤儿 torrent**。

## 2. 安全删除流程

正确的时序必须保证：
1. **先收集待清理的资源标识**（magnet_hash、video_path）
2. **再删除持久化数据**（config.json、数据库记录）
3. **最后释放外部资源**（磁盘缓存、内存句柄）

### 2.1 流程图

```
用户调用 DELETE /api/stars/{code}
        │
        ▼
┌─────────────────────────┐
│ 1. 从 config.json 移除   │
│    star 配置条目         │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 2. 查询数据库收集该 star  │
│    所有作品的 magnet_hash│
│    保存到本地列表        │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 3. 删除数据库记录        │
│    stars / titles /      │
│    social_posts          │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 4. 逐个调用              │
│    engine.remove_torrent │
│    清理磁盘与内存        │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 5. 使 stars 缓存失效     │
└─────────────────────────┘
```

### 2.2 关键代码结构

```python
async def delete_star(code: str, request: Request) -> dict[str, Any]:
    # 1. 配置层删除
    config = _load_config()
    config["stars"] = [s for s in config["stars"] if s.get("code") != code]
    _save_config(config)

    # 2. 先收集 magnet_hash（必须在数据库删除前完成）
    magnet_hashes: list[str] = []
    try:
        conn = duckdb.connect(DB_PATH)
        rows = conn.execute(
            "SELECT magnet_hash FROM titles WHERE star_code = ? AND magnet_hash IS NOT NULL",
            [code],
        ).fetchall()
        magnet_hashes = [h for (h,) in rows if h]
        conn.close()
    except Exception as e:
        log.warning(...)

    # 3. 数据库层删除
    db.delete_star_by_code(code)

    # 4. 缓存层删除（逐个处理，单点失败不影响后续）
    engine = request.app.state.engine
    for hash_str in magnet_hashes:
        try:
            await asyncio.to_thread(engine.remove_torrent, hash_str)
        except Exception as e:
            log.warning(...)

    # 5. 缓存失效
    invalidate_stars_cache()
    return {"code": code, "deleted": True}
```

## 3. 容错与幂等性

### 3.1 单点失败隔离

`engine.remove_torrent()` 可能因为文件被占用、libtorrent 句柄状态异常等原因失败。必须：
- **逐个 try/except 包裹**，一个失败不影响其他 torrent 的清理
- **记录 warning 日志**，便于人工排查
- 不因为单个 torrent 清理失败而回滚数据库删除（女优记录应当被删除）

### 3.2 重复删除安全

- `engine.remove_torrent()` 内部应当幂等：torrent 不存在时返回 `False`，不抛异常
- `db.delete_star_by_code()` 内部使用 `DELETE ... WHERE id = ?`，重复执行无影响
- `config.json` 的去重过滤天然幂等

### 3.3 数据库事务边界

`delete_star_by_code` 在一个连接内完成：
```sql
DELETE FROM social_posts WHERE star_id = ?;
DELETE FROM titles WHERE star_id = ?;
DELETE FROM stars WHERE id = ?;
COMMIT;
```
三条语句在同一个事务中，保证原子性。如果事务失败，数据库状态一致，可以重新调用 `delete_star`。

## 4. 孤儿 torrent GC

即使删除流程正确，仍可能因以下原因产生孤儿 torrent：
- 删除流程执行期间后端崩溃
- `engine.remove_torrent()` 某次调用失败且未重试
- 旧 bug 遗留（如此次 `IPVR-317` 案例）

### 4.1 检测逻辑

周期性地（例如每天一次，或启动时）扫描 `cache/torrent/` 目录，对比数据库中的 `magnet_hash`：

```python
def orphaned_hashes(cache_dir: str, db_path: str) -> list[str]:
    disk_hashes = {
        name for name in os.listdir(cache_dir)
        if len(name) == 40 and os.path.isdir(os.path.join(cache_dir, name))
    }
    conn = duckdb.connect(db_path)
    try:
        db_hashes = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT magnet_hash FROM titles WHERE magnet_hash IS NOT NULL"
            ).fetchall()
        }
    finally:
        conn.close()
    return sorted(disk_hashes - db_hashes)
```

### 4.2 清理策略

发现孤儿 hash 后：
1. 尝试调用 `engine.remove_torrent(hash)`（如果 torrent 已加载到内存）
2. 如果引擎中没有，直接删除 `cache/torrent/<hash>/` 目录
3. 记录 info 日志，汇报清理数量

### 4.3 与 `_enforce_cache_limit` 的关系

`TorrentEngine._enforce_cache_limit()` 在缓存超过阈值时触发驱逐，但它只驱逐 `engine.torrents` 中已知的 torrent。**孤儿 torrent 不在 `engine.torrents` 中，因此永远不会被 cache eviction 清理**，只会持续占用磁盘空间。这就是必须引入独立 GC 的原因。

## 5. 建议实现清单

- [x] 修复 `delete_star` 时序 bug：先查 magnet_hash 再删数据库
- [ ] 启动时扫描并清理孤儿 torrent（`lifespan` 中调用）
- [ ] 提供手动触发 GC 的 API：`POST /api/cache/gc-orphans`
- [ ] 在 `/api/cache/metrics` 中汇报孤儿 torrent 数量
- [ ] 删除操作记录审计日志（谁、何时、删除了哪个 star、清理了几个 torrent）

## 6. 相关文件

| 文件 | 职责 |
|---|---|
| `backend/routers/stars.py` | `delete_star` 端点 |
| `core/db/crud.py` | `delete_star_by_code` 数据库事务 |
| `backend/services/torrent_engine.py` | `remove_torrent` 磁盘与内存清理 |
| `docs/star-archive/deletion-design.md` | 本文档 |
