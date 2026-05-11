# Bootstrap-first 验证

> 适用：`backend/services/torrent_engine.py` — `_on_metadata()`
> 目标：用秒级 lseek 扫描替代分钟级 hash recheck

---

## 问题

当 torrent 从本地缓存恢复（`params.ti = lt.torrent_info(path)`）且 libtorrent 报告 `state=finished` 时：

**旧逻辑**：无条件 `force_recheck()` → 6GB 文件 hash 校验 → **5-15 分钟 blocking**

在这期间：
- `checking_files` 状态不响应 `set_piece_deadline()`
- 前端看到"校验中"，无法播放
- 即使文件数据实际完好，也必须等完 recheck

---

## 方案

```
finished torrent
    │
    v
┌─────────────────────────────┐
│ tracker._bootstrap_from_    │  ← SEEK_HOLE lseek 扫描
│   filesystem()              │     几秒完成
│                             │
│ tracker._overlay_have_      │  ← strict=True 同步 have_piece
│   piece(strict=True)        │
└─────────────┬───────────────┘
              │
    ├─ head_ready=True ──┐
    │                     v
    │          ┌──────────────────┐
    │          │ skip recheck     │
    │          │ info["ready"]=T  │
    │          │ return (秒级)    │
    │          └──────────────────┘
    │
    └─ head_ready=False ──► force_recheck()
                              (慢路径，数据确实缺失)
```

---

## 代码

```python
if not info.get("_recheck_done"):
    status = handle.status()
    if status.state == lt.torrent_status.finished:
        tracker = info.get("tracker")
        if tracker:
            tracker._bootstrap_from_filesystem()
            tracker._overlay_have_piece(strict=True)
            if tracker.head_ready():
                info["_recheck_done"] = True
                info["ready"] = True
                log.info(f"bootstrap-first: {hash_str[:12]}... data intact, skip recheck")
                return
        # Slow path: stale have_pieces or missing data
        handle.force_recheck()
        info["_recheck_done"] = True
```

---

## 关键设计

1. **lseek 扫描 vs hash 校验**
   - lseek: O(pieces)，只检查磁盘块是否分配 → **几秒**
   - hash: O(bytes)，逐块计算 SHA-1 → **几分钟**

2. **strict=True 双向同步**
   - `have_piece=True` 但 lseek 发现 hole → 跳过（防止 page-cache 误报）
   - `have_piece=False` 但 lseek 发现数据 → 清除 VERIFIED（recheck 后的清零）

3. **只做一次**
   - `_recheck_done` flag 防止重复触发
   - `_on_metadata` 可能被 `add_torrent` 重复调用（existing 路径）

---

## 效果

| 场景 | 旧逻辑 | 新逻辑 |
|------|--------|--------|
| 6GB 文件完好 | 5-15 min recheck | **几秒 lseek，秒 ready** |
| 6GB 文件缺失 tail | 5-15 min recheck + 重下 | lseek 发现缺失 → recheck + 重下 |
| 2GB 文件完好 | 2-5 min recheck | **秒 ready** |

对于"已完成且数据完好"的缓存（最常见场景），从分钟级降到秒级。
