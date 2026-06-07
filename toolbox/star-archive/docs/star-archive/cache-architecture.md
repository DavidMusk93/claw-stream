# Cache 模块架构文档

## 第一性原理

> **流畅播放 = 数据在需要时可用**

一切设计围绕这个唯一目标展开。缓存不是"存得越多越好"，而是"在播放器请求的那一刻，目标数据必须已经躺在磁盘上"。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                        Cache Module                           │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
│  │  Tiered      │   │  PieceState  │   │  Video       │    │
│  │  Cache       │   │  Tracker     │   │  Stream      │    │
│  │  (L1-L4)     │   │  (bitmap)    │   │  (hole det)  │    │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘    │
│         │                  │                  │              │
│         ▼                  ▼                  ▼              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                 TorrentEngine                         │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐ │   │
│  │  │ libtorrent│  │ sparse │  │ seek   │  │ GC     │ │   │
│  │  │ session  │  │ file   │  │ priority│  │ (orphan│ │   │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘ │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
│  │  stream      │   │  cache       │   │  torrents    │    │
│  │  router      │   │  router      │   │  router      │    │
│  └──────────────┘   └──────────────┘   └──────────────┘    │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 核心组件

### 1. TorrentEngine

职责：libtorrent 会话生命周期、缓存管理、分级淘汰。

#### 1.1 缓存分级（L1/L2/L3/L4）

| Tier | 条件 | 保护级别 | 淘汰策略 |
|---|---|---|---|
| **L1 (hot)** | 24h 内播放过 | 最高 | soft limit 不驱逐；hard limit 才驱逐 |
| **L2 (warm)** | 100% 完成 + 7d 内访问 | 高 | 不驱逐 |
| **L3 (seed)** | 100% 完成 + 冷数据 (>7d) | 中 | 先 punch hole（L3→L4），再驱逐 |
| **L4 (fragment)** | 未完成 + 冷数据 | 低 | 直接驱逐 |

**like 保护**：liked 作品在 scoring 中 +5000 分，相当于一个独立保护层级。

#### 1.2 滑动窗口下载策略

- **prefetch**：head 2%（moov 优先）
- **play**：moov + 极小窗口（严格 head only）
- **playing**：当前位置 ±30 piece（约 2-4 分钟缓冲）
- **seek**：目标位置 ±15 piece

已下载 piece 保留（priority=1），不在窗口内的未下载 piece 设为 0。避免"遍地开花"。

#### 1.3 稀疏文件 + Hole 检测

- Linux sparse file：未下载区域不占磁盘
- `SEEK_HOLE` / `SEEK_DATA`：O(1) 检测 hole
- `FALLOC_FL_PUNCH_HOLE`：L3→L4 降级时释放中间 piece 磁盘空间

---

### 2. PieceStateTracker

3×int bitmap 状态机：
- `VERIFIED`：SEEK_HOLE 确认有数据，或 libtorrent piece_finished_alert
- `DOWNLOADING`：priority/deadline 已设置，等待 peers
- `CORRUPT`：hash failed 或读取到零数据

O(1) `head_ready()`：预计算 moov mask + POPCNT。

---

### 3. VideoStream

- **mmap 读取**：避免用户态拷贝
- **piece 级 hole 检测**：`_detect_hole_offset` 按 piece 边界切分 chunk
- **partial return**：hole 在中间时返回 hole 前的有效数据，播放器可继续解码
- **corrupt 自修复**：verified piece 读零 → 标记 corrupt + trigger re-download

---

### 4. Cache Router

- `GET /api/cache`：列出所有有数据的缓存项
- `GET /api/cache/metrics`：统计（completed / downloading / used / max）
- `POST /api/cache/gc-orphans`：清理磁盘存在但 DB 无记录的孤儿 torrent
- `DELETE /api/cache/{hash}`：手动删除

---

## 关键数据流

### 播放流程

```
1. 前端点击 play
   → POST /torrent/add（如果尚未加载）
   → GET  /api/check/{hash}（轮询 head_ready）

2. head_ready = true
   → GET /stream/{hash}（Range 请求）
   → stream_router 调用 read_video_range
   → seek_priority 设置 urgent piece
   → _read_once 通过 mmap 读取
   → _detect_hole_offset 检测零数据
   → 返回 206 Partial Content

3. 播放器持续请求
   → update_play_progress 滑动窗口 ±30 piece
   → libtorrent 自动下载窗口内 piece
```

### 缓存淘汰流程

```
1. add_torrent 触发 _enforce_cache_limit
2. 如果 used > soft_limit (95%)
   → 构建候选列表（排除 hot + liked）
   → 按 _cache_score 排序（最低分优先）
   → L3 → punch hole（保留 head+tail）
   → L2/L4 → remove_torrent（删除文件）
3. _periodic_clean 每 60s 重复检查
```

---

## 当前 Bug 与修复

### Bug 1：_enforce_cache_limit 只驱逐一个 torrent

**症状**：缓存远超 soft limit 时，一次只驱逐一个，需要多次 60s 周期才能降到限制内。

**根因**：设计为"每周期一个"，但未考虑批量添加场景。

**修复**：循环驱逐，直到缓存降到 soft limit 以下。

### Bug 2：_cleanup_orphaned 只在启动时执行

**症状**：运行过程中 `_readd_torrent` 或手动删除后，旧缓存目录残留。

**修复**：加入 `_periodic_clean`，每 60s 执行一次。

### Bug 3：remove_torrent sleep 0.5s 阻塞线程

**症状**：线程池中的线程被 sleep 0.5s，降低并发效率。

**修复**：缩短到 0.1s，或改为轮询检查文件是否可删除。

### Bug 4：hot 但未 liked 的 torrent 可被驱逐

**症状**：24h 内播放过但未 like 的作品，在 soft limit 时可能被驱逐。

**修复**：soft limit 时保护所有 hot torrent（无论是否 liked）。liked 只影响 warm/seed 的 scoring。

---

## 最佳实践

1. **缓存大小 = 磁盘 60%**：留 40% 给系统和其他服务，避免 IO 竞争导致播放卡顿。
2. **优先 moov**：没有 moov 的 MP4 无法播放，moov 必须在任何其他数据之前下载。
3. **保留已下载 piece**：窗口滑动时，旧窗口的已下载 piece 设为 priority=1（保留），不丢弃。避免反复重新下载。
4. **punch hole 而非全删**：L3 降级时只删除中间 piece，保留 head+tail。用户重新播放时不需要重新下载头部。
5. **hole 绝不返回播放器**：stream 层检测到零数据时返回 416 或等待，绝不把零数据发给解码器。
6. **一个改动一个 commit**：缓存逻辑敏感，每次只改一处，便于回滚。
