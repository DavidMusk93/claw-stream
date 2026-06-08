# Star Archive — 系统架构

> 适用项目：``
> 后端版本：FastAPI + libtorrent 2.0.11
> 核心目标：按需下载、分级缓存、无缝播放

---

## 1. 服务架构

```
用户浏览器
    │
    │ HTTPS :443
    v
┌─────────────────────────────────────┐
│ Caddy (reverse proxy)               │
│ - 自动 TLS (Let's Encrypt)          │
│ - 证书自动续期                      │
└─────────────────────────────────────┘
    │
    ├─ / ────────────────► Nuxt frontend (:3000)
    ├─ /api/* ───────────► FastAPI backend (:8765)
    ├─ /stream/* ────────► FastAPI backend (:8765)
    └─ /torrent/* ───────► FastAPI backend (:8765)
```

---

## 2. 播放流程

### 2.1 完整交互

```
用户点击播放
    │
    v
GET /api/check/<hash>  ──►  check_stream()
    │                           - find_video_state() 扫描文件
    │                           - 若 checking_files → head_ready=false
    │
    ├─ head_ready = true ──► video.src = /stream/<hash>
    │                           - stream_video() 读取 Range
    │                           - seek_priority() 触发 urgent 下载
    │                           - 返回 206 Partial Content
    │
    └─ head_ready = false ─► POST /torrent/add
                              - add_torrent() 添加 magnet
                              - _on_metadata() 设置 play priority
                              - 轮询 /torrent/status (1s 间隔)
                              - head_ready 后播放
```

### 2.2 引擎状态机

```
add_torrent(magnet)
    │
    v
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│ metadata_wait │────►│ checking_files│────►│ downloading   │
│ (DHT/tracker) │     │ (hash verify) │     │ (priority=7)  │
└───────────────┘     └───────────────┘     └───────────────┘
       │                                           │
       │                                           v
       │                                    head+tail urgent
       │                                           │
       │                                           v
       │                                    bootstrap-first
       │                                    (finished → skip recheck)
       │                                           │
       v                                           v
metadata_received_alert                    cache warming retry
       │                                    (every 10s re-apply)
       v                                           │
_on_metadata() ◄──────────────────────────────────┘
- 选 hhd800 视频文件
- 扫描/缓存 moov 范围
- 设置 file priority=4
- _apply_play_priority() → head+tail=7
```

### 2.3 预缓存 vs 播放模式

| | 预缓存 (prefetch) | 播放模式 |
|---|---|---|
| 触发 | 页面加载后自动 | 用户点击播放 |
| 策略 | piece 0~2%: prio=4 | head+tail: prio=7 + deadline=0 |
| 其余 | prio=0 | prio=0 |
| 目的 | 播放按钮显示绿色徽章 | moov 就绪即可播放 |
| 磁盘 | ~100-200MB/部 | ~60MB head + ~60MB tail |

---

## 3. 关键子系统

### 3.1 PieceStateTracker — 位图状态机

3 个 Python `int` bitmap 编码 4 个状态：

```python
_verified    = 0  # bit p = 1 → VERIFIED
_corrupt     = 0  # bit p = 1 → CORRUPT
_downloading = 0  # bit p = 1 → DOWNLOADING
# all 0 → NOT_DOWNLOADED
```

- `head_ready()`: `_moov_vc == _moov_pc` → O(1) 整数比较
- `verified_count()`: `_verified.bit_count()` → O(1) POPCNT
- `request_pieces()`: 位掩码过滤 + 批量 `prioritize_pieces()`

详见 `piece-tracker.md`。

### 3.2 Bootstrap-first 验证

finished torrent → `SEEK_HOLE` lseek 扫描 → 若 `head_ready=True` → 跳过 `force_recheck()`

**之前**: 无条件 recheck → 5-15 分钟 blocking
**之后**: 数据完好时秒 ready，只有缺失才回退 recheck

详见 `bootstrap-first.md`。

### 3.3 分级缓存 (Tiered Cache)

```
L1 hot:      24h 内播放过 → 永不淘汰
L2 warm:     100% 完成 + 7 天内访问 → 高优先级保留
L3 seed:     100% 完成 + 冷存 → 可 punch hole 降级
L4 fragment: 未完成 + 冷存 → 优先淘汰
```

淘汰决策用 `_cache_score()` 替代纯 LRU：
```
score = (play_bonus + completion) / size_gb × heat_decay + play_bonus
```

详见 `tiered-cache.md`。

### 3.4 Cache-warming 重试

`get_status()` 每秒被前端 poll。若 `head_ready=False` 且超过 10 秒未重试：
- 自动重新 `_apply_play_priority()`
- 防止 peer 断线后 priority 失效

### 3.5 GC touch 保护

`/stream/{hash}` 和 `/api/check/{hash}` 每次调用都会 `engine.touch(hash_str)`：
- 更新 `last_access`
- 标记 `_last_play_time` 和 `_play_count`

防止"正在播放的 torrent 被 GC 驱逐"。

---

## 4. 稀疏文件与 Hole 检测

### 4.1 SEEK_DATA / SEEK_HOLE

```python
# 检测 offset 处是否有真实数据（非 sparse hole）
os.lseek(fd, offset, os.SEEK_DATA) == offset

# 检测 [start, end] 范围内是否有 hole
os.lseek(fd, start, os.SEEK_HOLE) > end
```

比 `not any(data)` 全零检测更可靠：
- libtorrent checking_files 期间会临时清零 piece
- MP4 ftyp 开头就是 `00 00`，全零检测会误判

### 4.2 stream 读取流程

```
read_video_range(start, end)
    │
    ├─► seek_priority(start, end)  # 设置 urgency
    │
    ├─► _read_once(path, start, chunk_size)
    │   ├─► mmap 读取
    │   └─► 若 16KB 全 0 → hole detected
    │
    ├─► _detect_hole() → SEEK_DATA 确认
    │
    └─► 若 hole: 等待 0.1s，重试（最多 2s）
            ├─► libtorrent 下载该 piece
            └─► 再次读取 → 有数据 → 返回
```

Hole 时返回空 bytes → 调用方返回 416（不是 200/206 含零数据）。

---

## 5. 故障排查速查

| 症状 | 排查 | 解决 |
|------|------|------|
| 点击播放无反应 | `curl /api/check/<hash>` → head_ready? | false → 等 30-60s，或检查 peers |
| 播放中黑屏/卡顿 | 检查 `state=checking_files` | 等 checking 完成（503 自动重试） |
| seek 卡住 | 检查 video-stream.log 的 hole timeout | 正常，libtorrent 在 urgent 下载 |
| 进度 100% 但无法播放 | moov 在尾部（非 faststart） | 这类文件无法边下边播 |
| 磁盘瞬间爆满 | 检查 piece priorities | 应只有 head+tail=7，其余=0 |
| 后端 502 | `journalctl -u caddy-claw` | 检查 upstream timeout |

### 5.1 常用命令

```bash
# 查看 torrent 状态
curl -s http://localhost:8765/torrent/status/<hash> | python3 -m json.tool

# 检查稀疏文件真实大小
stat --format="逻辑=%s 实际=%b*%B=%B" cache/torrent/<hash>/.../*.mp4

# 查看后端日志
journalctl -u star-archive-backend -f

# 检查 piece priorities (debug)
python3 -c "
import libtorrent as lt
s = lt.session()
# ...
"
```

---

## 6. 监控端点

```bash
GET /api/health              → {"status": "ok"}
GET /api/cache               → 缓存列表 + 总大小
GET /api/cache/metrics       → 完成数/下载中/已用/上限
GET /torrent/status/<hash>   → 单个 torrent 完整状态（含 tier）
GET /api/check/<hash>        → head_ready / cached / mime
```
