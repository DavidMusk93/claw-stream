# Tiered Cache

> Applies to: `backend/services/torrent_engine.py` — `_enforce_cache_limit()`  
> Goal: Replace pure time-based eviction with data-value scoring, improving cache utilization from 80% → 95%, while guaranteeing the cache never exceeds its configured upper bound.

---

## Table of Contents

1. [Four-tier Classification](#four-tier-classification)
2. [Scoring Function](#scoring-function)
3. [Progressive Eviction](#progressive-eviction)
4. [L3 Punch Hole](#l3-punch-hole)
5. [Comparison with Legacy LRU](#comparison-with-legacy-lru)

---

## Four-tier Classification

```
L1 hot        ──►  Played within 24h        ──►  Never evict at soft limit
L2 warm       ──►  100% complete + accessed within 7d  ──►  High retention
L3 seed       ──►  100% complete + cold (>7d)          ──►  Punch hole eligible
L4 fragment   ──►  Incomplete + cold                   ──►  Evict first
```

---

## Scoring Function

```python
def _cache_score(info) -> float:
    """Higher = more valuable, less evictable."""
    now = time.time()
    last_play = info.get("_last_play_time", 0)
    progress = info.get("progress", 0)
    size = info.get("video_size", 1024)
    play_count = info.get("_play_count", 0)
    hash_str = info.get("hash", "")

    hours_since_play = (now - last_play) / 3600 if last_play else 9999
    heat = math.exp(-hours_since_play / 168)  # 7-day half-life

    play_bonus = 1000.0 * play_count
    completion_score = progress * 10
    size_gb = size / (1024 ** 3)
    value_per_gb = (play_bonus + completion_score) / max(size_gb, 0.1)

    score = value_per_gb * heat + play_bonus

    # Like bonus: liked works get strong protection
    if hash_str in self.liked_hashes:
        score += 5000.0
    else:
        score -= 2000.0

    return score
```

**Factor Breakdown:**

| Factor | Weight | Description |
|--------|--------|-------------|
| `play_bonus` | 1000× per play | Played torrents are an order of magnitude more valuable |
| `completion` | 10× per % | 100% = 1000 pts; re-download cost is high |
| `value_per_gb` | Density | A completed 6GB file scores higher than an incomplete 6GB file |
| `heat` | Exponential decay | 7-day half-life; recent plays score higher |
| `like` | +5000 / –2000 | Liked works receive strong protection |

---

## Progressive Eviction

```python
def _enforce_cache_limit(self):
    total = self._get_cache_size()
    soft_threshold = int(self.max_size_bytes * 0.95)
    hard_threshold = self.max_size_bytes          # 100%: true upper limit
    available = _get_disk_available_bytes(self.cache_dir)
    emergency = available < self.min_free_bytes   # OS free-space guard

    if not emergency and total <= soft_threshold:
        return

    force_evict_hot = total > hard_threshold or emergency

    while True:
        total = self._get_cache_size()
        available = _get_disk_available_bytes(self.cache_dir)
        emergency = available < self.min_free_bytes
        if total <= soft_threshold and not emergency:
            break

        candidates = [
            (h, i) for h, i in self.torrents.items()
            if force_evict_hot or emergency or self._get_tier(i) != "hot"
        ]
        candidates.sort(key=lambda x: self._cache_score(x[1]))
        hash_str, info = candidates[0]
        tier = self._get_tier(info)

        # L3 → punch hole downgrade
        if tier == "seed" and info.get("progress", 0) >= 99.9:
            freed = self._punch_hole_middle_pieces(hash_str)
            if freed > 0:
                continue

        # L2/L4 or punch-hole-insufficient L3 → full eviction
        self.remove_torrent(hash_str)
```

**Progressive**: Processes one torrent per iteration, avoiding IO spikes. The loop continues until usage drops below the soft threshold.

**True upper limit**: The hard threshold is exactly `max_size_bytes` (100%). Cache usage is never allowed to exceed the configured limit.

**Emergency free-space guard**: Even when cache usage is below `max_size_bytes`, if the partition's available space drops below the configured reserve (`min_free_bytes`), eviction runs in emergency mode and ignores tier / like protection to prevent disk exhaustion.

---

## L3 Punch Hole

For completed but cold torrents, keep head+tail and release middle pieces:

```python
def _punch_hole_middle_pieces(self, hash_str):
    tracker = self.torrents[hash_str].get("tracker")
    path = self.torrents[hash_str].get("video_path")

    head_end = tracker.start_piece + 30
    tail_start = tracker.end_piece - 30

    fd = os.open(path, os.O_WRONLY)
    try:
        for p in range(tracker.start_piece, tracker.end_piece + 1):
            if p < head_end or p > tail_start:
                continue
            if not tracker.is_verified(p):
                continue
            start = p * tracker.piece_length - tracker.file_offset
            os.fallocate(fd, os.FALLOC_FL_PUNCH_HOLE | os.FALLOC_FL_KEEP_SIZE,
                         start, tracker.piece_length)
    finally:
        os.close(fd)
```

**Effect**: A 6GB file → keeps ~120MB (head+tail), releases ~5.88GB.

- Users can still start playback immediately (moov in head is preserved; tail is also preserved)
- Seeking to the middle requires re-download

---

## Comparison with Legacy LRU

| Aspect | Legacy LRU | Tiered Cache |
|--------|------------|--------------|
| Threshold | 80% | 95% soft / 100% hard |
| Decision basis | `last_access` timestamp | Play history + completion + heat + value density |
| Eviction granularity | Whole torrent | Whole torrent or punch hole (L3→L4) |
| Playback protection | 5-minute `last_access` | L1 (24h) + `touch()` real-time update |
| Large-file penalty | None | `value_per_gb` lowers score of large incomplete files |
| Like protection | None | +5000 / –2000 scoring modifier |
| Free-space guard | None | Emergency eviction when available disk < reserve |
| Hard upper limit | 120% of max | 100% of max (never exceed) |
