# BitTorrent 引擎经验手册 —— 精细下载控制与缓存管理

> 适用项目：Star Archive（`toolbox/star-archive/`）
> 引擎版本：libtorrent 2.0.8 (Rasterbar)
> 核心目标：按需下载、稀疏文件、LRU 缓存、无缝播放

---

## 1. 设计目标

| 目标 | 约束 |
|------|------|
| 按需下载 | 不播放时不下载，播放时只拉取 head + tail + 滑动窗口 |
| 无缝播放 | head_ready 后立即可播，seek 后 5–15s 内恢复 |
| 缓存可控 | 15GB 上限，LRU 淘汰，稀疏文件不浪费磁盘 |
| 秒开体验 | 本地缓存 `.torrent` metadata，跳过 DHT/tracker 发现 |

---

## 2. libtorrent 2.0 行为陷阱（血泪经验）

### 2.1 默认标志：paused = True

```python
params = lt.parse_magnet_uri(magnet)
# params.flags 默认包含 lt.torrent_flags.paused
```

**后果**：torrent 添加后永远不会连接 tracker/DHT，peers 永远为 0。
**修复**：
```python
params.flags &= ~lt.torrent_flags.auto_managed
params.flags &= ~lt.torrent_flags.seed_mode
params.flags &= ~lt.torrent_flags.paused   # ← 必须显式移除
```

### 2.2 auto_managed 与 seed_mode

| 标志 | 默认 | 禁用原因 |
|------|------|----------|
| `auto_managed` | True | libtorrent 会自动覆盖 piece priority，破坏滑动窗口策略 |
| `seed_mode` | False | 若启用，稀疏文件会被视为"已完成"，progress 直接 100%，但数据不存在 |

### 2.3 piece priority 与 "finished" 状态

libtorrent 的 `finished` 判定：**所有 priority > 0 的 piece 都已下载**。

```
场景 A：全部 piece priority = 1
        → finished 只有全部 2668 个 piece 下载后才触发 ✓
        → 但会下载整个文件，缓存失控 ✗

场景 B：窗口外 priority = 0，窗口内 priority = 7
        → head+tail（~60 piece）完成后即触发 finished
        → peers 从 19 骤降到 2–3，seek 到新区域极慢 ✗
```

**最终策略**：
- 窗口外 `priority = 0`（严格按需）
- stream 请求到达时，`seek_priority()` **同时**设置 `deadline=0` + `priority=7`
- libtorrent 立即从 `finished` → `downloading`，全速拉取

### 2.4 `.torrent` 文件加载与文件校验

当 `params.ti = lt.torrent_info(path)` 时：
- libtorrent **立即**开始 `checking_files` 状态
- 5GB 稀疏文件校验约需 20–30 秒
- 期间 `h.status()` 不阻塞，但 CPU 占用 ~22%

**陷阱**：`metadata_received_alert` **不会**触发（metadata 来自本地文件）。
**修复**：在 `add_torrent()` 中手动调用 `_on_metadata(handle)`，且必须先注册 `info` 到 `self.torrents` 再调用，否则 `_on_metadata()` 因查不到记录而提前返回。

### 2.5 prioritize_files 触发 torrent_checked_alert

```
torrent_checked_alert
    → _on_metadata() → handle.prioritize_files()
        → 再次触发 torrent_checked_alert
            → _on_metadata() ... 无限循环
```

**修复**：`torrent_checked_alert` handler 中**不再**调用 `_on_metadata()`。

---

## 3. 按需下载策略：滑动窗口

### 3.1 优先级分层

```
Priority 7 (urgent):  head (前 30 piece) + tail (后 30 piece) + 播放窗口 (±30 piece)
Priority 0 (skip):    其他所有 piece
Deadline 0:           stream 请求对应的 Range piece（强制立即下载）
```

### 3.2 窗口生命周期

```
[添加 torrent] ──► _apply_play_priority(window_pcs=0)
                       │
                       ▼
                head + tail = 7, 其余 = 0
                       │
    [前端报告进度] ───► update_play_progress(window_pcs=30)
                       │
                       ▼
                当前播放头 ±30 piece = 7, 其余 = 0
                       │
        [用户 seek] ──► apply_seek_priority(window_pcs=15)
                       │
                       ▼
                seek 目标 ±15 piece = 7, 其余 = 0
                       │
    [stream 请求] ───► seek_priority() 设置 deadline=0 + priority=7
                       │
                       ▼
                即使 torrent 在 finished 状态也立即恢复 downloading
```

### 3.3 ASCII 流程图：从点击播放到数据就绪

```
┌─────────────┐     click      ┌──────────────┐
│  TitleCard  │ ─────────────► │   openVideo  │
└─────────────┘                └──────┬───────┘
                                      │ extract hash
                                      ▼
┌─────────────┐   modalOpen=true   ┌──────────────┐
│  VideoModal │ ◄───────────────── │   index.vue  │
└──────┬──────┘                    └──────────────┘
       │
       │ watch([hash, isOpen])
       ▼
┌─────────────────────┐
│  waitForHeadReady   │ ◄──── 循环 checkHeadReady + /torrent/add
│    (timeout 180s)   │
└──────────┬──────────┘
           │ ready = true
           ▼
┌─────────────────────┐
│  video.src = url    │
│  video.load()       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐     206 Partial Content      ┌─────────────┐
│   <video> canplay   │ ◄──────────────────────────── │  /stream/*  │
└──────────┬──────────┘                               └──────┬──────┘
           │                                                 │
           │ play()                                          │ read_video_range
           ▼                                                 │
┌─────────────────────┐                                      │
│    播放中...         │                                      ▼
│  onTimeUpdate(10s)  │ ──► reportProgress ──► ┌─────────────────────┐
│    onSeeked         │ ──► reportSeek ───────► │ _set_stream_window  │
└─────────────────────┘                         │   (update window)   │
                                                └─────────────────────┘
                                                          │
                              ┌───────────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │   libtorrent 引擎    │
                    │  ┌───────────────┐  │
                    │  │ checking_files│  │  ← 加载本地 .torrent 时
                    │  └───────┬───────┘  │
                    │          │           │
                    │          ▼           │
                    │  ┌───────────────┐  │
                    │  │  downloading  │  │  ← head+tail / 窗口 piece
                    │  └───────┬───────┘  │
                    │          │           │
                    │          ▼           │
                    │  ┌───────────────┐  │
                    │  │   finished    │  │  ← 所有 priority>0 完成
                    │  └───────────────┘  │
                    │          ▲           │
                    │          │           │
                    │   seek_priority()    │  ← deadline=0 + priority=7
                    │   自动恢复 downloading│
                    └─────────────────────┘
```

### 3.4 stream 读取与 hole 处理

```
read_video_range(start, end)
    │
    ├─► seek_priority(start, end)    # 设置 urgency
    │
    ├─► open(path).seek(start)
    │   ├─► 读取 chunk
    │   ├─► 若 16KB 全 0 → hole detected
    │   │
    │   └─► 等待 0.5s，重试（最多 15s）
    │       ├─► libtorrent 下载该 piece
    │       └─► 再次读取 → 有数据 → 返回
    │
    └─► 若 15s 后仍有 hole → 返回已读取部分（可能含 0）
```

---

## 4. Cache 管理策略

### 4.1 三层防护

```
┌─────────────────────────────────────────┐
│  Layer 1: 稀疏文件（内核级）              │
│  - 未下载的 piece 不占实际磁盘           │
│  - `st_blocks * 512` 反映真实占用        │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  Layer 2: LRU 淘汰（应用级）              │
│  - 上限 15GB，阈值 80%（12GB）           │
│  - 按 last_access 排序，淘汰最旧         │
│  - 播放中（5 分钟内）的 torrent 受保护   │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  Layer 3: 孤儿清理（启动安全）            │
│  - self.torrents 为空时 → 跳过清理       │
│  - 防止独立脚本/测试误删生产缓存          │
└─────────────────────────────────────────┘
```

### 4.2 缓存统计口径

| 指标 | 计算方式 | 用途 |
|------|----------|------|
| `local_size` | `st.st_blocks * 512` | 判断 head_ready、显示实际占用 |
| `video_size` | `ti.files()[idx].file_size()` | 前端进度条分母 |
| `progress` | `lt.status().progress * 100` | libtorrent 内部进度 |
| cache 总占用 | `os.walk()` + `st_blocks * 512` | LRU 淘汰触发条件 |

### 4.3 关键教训：永远不要信任逻辑文件大小

```python
# ❌ 错误：会返回 5.3GB（稀疏文件逻辑大小）
os.path.getsize(path)

# ✓ 正确：返回 123MB（实际磁盘占用）
st = os.stat(path)
real_size = st.st_blocks * 512
```

---

## 5. 典型问题时间线

| 时间 | 现象 | 根因 | 修复 |
|------|------|------|------|
| Phase 1 | 点击视频无反应 | Vue `watch(() => props.hash)` 非 immediate，`isOpen` 更新时序导致漏触发 | `watch([() => props.hash, isOpen])` |
| Phase 2 | 卡在"连接种子" | `lt.parse_magnet_uri` 默认 `paused=True` | `params.flags &= ~lt.torrent_flags.paused` |
| Phase 2 | tracker 丢失 | 前端只传 bare hash magnet | 后端 `_resolve_magnet()` 从数据库补全 tracker |
| Phase 3 | progress 虚高 99% | `auto_managed=True` 覆盖 piece priority | 禁用 `auto_managed` + `seed_mode` |
| Phase 3 | 误删 1.1GB 缓存 | 独立脚本 `TorrentEngine()` 触发 `_cleanup_orphaned()` | `self.torrents` 为空时跳过清理 |
| Phase 4 | 下载部分就算完成 | piece priority 0 导致 `finished` 过早触发，seek 后 peers 骤降 | `seek_priority()` 同时设置 `priority=7` + `deadline=0` |
| Phase 4 | 后端启动卡住 | `torrent_checked_alert` → `_on_metadata()` → `prioritize_files()` → 无限循环 | 移出 alert handler，改在 `add_torrent()` 中显式调用 |
| Phase 4 | `ready=False` | `_on_metadata()` 在 `info` 注册前调用，查不到记录直接返回 | 调整调用顺序：先注册 `info`，再调 `_on_metadata()` |

---

## 6. 最小可用配置模板

```python
import libtorrent as lt

params = lt.parse_magnet_uri(magnet)
params.save_path = save_path

# 三件套：禁用自动管理、禁用种子模式、移除暂停
params.flags &= ~lt.torrent_flags.auto_managed
params.flags &= ~lt.torrent_flags.seed_mode
params.flags &= ~lt.torrent_flags.paused

# 本地 metadata 秒开（可选）
torrent_path = os.path.join(save_path, f"{hash_str}.torrent")
if os.path.exists(torrent_path):
    params.ti = lt.torrent_info(torrent_path)

handle = session.add_torrent(params)

# 若从本地加载，立即执行 metadata 后处理
if params.ti is not None:
    _on_metadata(handle)   # 必须在 info 注册到字典之后
```

---

## 7. 监控与调试命令

```bash
# 实时查看 torrent 状态
curl -s http://127.0.0.1:8765/torrent/status/<hash>

# 检查稀疏文件真实大小
stat --format="逻辑=%s 实际=%b*%B=%B" cache/torrent/<hash>/.../*.mp4

# 查看 libtorrent 端口监听
ss -tlnp | grep 6881

# 后端日志（含 alert）
journalctl -u star-archive-backend.service -f

# 直接测试 stream 某段数据是否全 0
curl -s --range "bytes=2000000000-2000000099" \
  http://127.0.0.1:8765/stream/<hash> | od -A x -t x1z | head
```

---

## 8. 待探索方向

1. **磁盘配额**：当前用 `st_blocks * 512` 估算，ext4/XFS 对稀疏文件支持良好，但 BTRFS/ZFS 行为待验证。
2. **多文件 torrent**：当前假设只有一个 hhd800 视频文件。若未来需要处理多版本（4K/1080P），需扩展 `_pick_video_file()`。
3. **预加载策略**：当前预热只下载 head+tail（~120MB）。若网络极不稳定，可预加载前 5% piece 作为缓冲垫。
4. **libtorrent 2.0 MMAP 后端**：稀疏文件读取性能在大量 seek 时可能退化，未来可评估 `set_piece_deadline` + `clear_piece_deadlines` 的精细控制。
