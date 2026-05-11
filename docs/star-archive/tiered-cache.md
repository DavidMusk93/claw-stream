# 分级缓存 (Tiered Cache)

> 适用：`backend/services/torrent_engine.py` — `_enforce_cache_limit()`
> 目标：用数据价值替代纯时间做淘汰决策，缓存利用率从 80% → 95%

---

## 四级分类

```
L1 hot        ──►  24h 内播放过  ──►  永不淘汰
L2 warm       ──►  100% 完成 + 7天内访问  ──►  高优先级保留
L3 seed       ──►  100% 完成 + 冷存(>7d)  ──►  可 punch hole 降级
L4 fragment   ──►  未完成 + 冷存  ──►  优先淘汰
```

---

## 评分函数

```python
def _cache_score(info) -> float:
    """Higher = more valuable, less evictable."""
    now = time.time()
    last_play = info.get("_last_play_time", 0)
    progress = info.get("progress", 0)
    size = info.get("video_size", 1024)
    play_count = info.get("_play_count", 0)

    hours_since_play = (now - last_play) / 3600 if last_play else 9999
    heat = math.exp(-hours_since_play / 168)  # 7-day half-life

    play_bonus = 1000.0 * play_count
    completion_score = progress * 10
    size_gb = size / (1024 ** 3)
    value_per_gb = (play_bonus + completion_score) / max(size_gb, 0.1)

    return value_per_gb * heat + play_bonus
```

**因素拆解：**

| 因素 | 权重 | 说明 |
|------|------|------|
| `play_bonus` | 1000×/次 | 播放过的 torrent 价值高一个数量级 |
| `completion` | 10×/% | 100% = 1000 pts，重新下载成本高 |
| `value_per_gb` | 密度 | 6GB 完成文件 > 6GB 未完成文件 |
| `heat` | 指数衰减 | 7 天半衰期，最近播放的分数更高 |

---

## 渐进淘汰

```python
def _enforce_cache_limit(self):
    total = self._get_cache_size()
    threshold = int(self.max_size_bytes * 0.95)  # 95% 阈值
    if total <= threshold:
        return

    # 排除 L1 (hot)
    candidates = [(h, i) for h, i in self.torrents.items()
                  if self._get_tier(i) != "hot"]
    candidates.sort(key=lambda x: self._cache_score(x[1]))
    hash_str, info = candidates[0]  # 只淘汰一个最冷的

    tier = self._get_tier(info)

    # L3 → punch hole 降级
    if tier == "seed" and progress >= 99.9:
        freed = self._punch_hole_middle_pieces(hash_str)
        if freed > 0 and new_size <= threshold:
            return

    # 其余 → 整文件删除
    self.remove_torrent(hash_str)
```

**渐进式**：每次只处理一个 torrent，避免 I/O 突刺。

---

## L4 Punch Hole

对 completed 但 cold 的 torrent，保留 head+tail，释放中间 pieces：

```python
def _punch_hole_middle_pieces(self, hash_str):
    # 保留 head+tail 各 30 pieces
    head_end = start_piece + 30
    tail_start = end_piece - 30

    for p in range(start_piece, end_piece + 1):
        if p < head_end or p > tail_start:
            continue
        if not tracker.is_verified(p):
            continue
        start = p * piece_length - file_offset
        os.fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE,
                     start, piece_length)
```

**效果**：6GB 文件 → 保留 120MB (head+tail)，释放 5.88GB。
- 用户仍能立即播放（moov 在 tail）
- seek 到中间时需要重新下载

---

## 与旧 LRU 的对比

| | 旧 LRU | 新分级缓存 |
|---|---|---|
| 阈值 | 80% | 95% |
| 决策依据 | last_access 时间戳 | 播放历史 + 完成度 + 热度 + 价值密度 |
| 淘汰粒度 | 整 torrent | 整 torrent 或 punch hole (L3→L4) |
| 播放保护 | 5 分钟 last_access | L1 (24h) + touch() 实时更新 |
| 大文件惩罚 | 无 | value_per_gb 降低大未完成文件分数 |
