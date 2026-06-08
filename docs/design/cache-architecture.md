# Cache Module Architecture

## Table of Contents

1. [First Principles](#first-principles)
2. [Architecture Overview](#architecture-overview)
3. [Core Components](#core-components)
   - [3.1 TorrentEngine](#31-torrentengine)
   - [3.2 PieceStateTracker](#32-piecestatetracker)
   - [3.3 VideoStream](#33-videostream)
   - [3.4 Cache Router](#34-cache-router)
4. [Key Data Flows](#key-data-flows)
   - [4.1 Playback Flow](#41-playback-flow)
   - [4.2 Cache Eviction Flow](#42-cache-eviction-flow)
5. [Fixed Bugs](#fixed-bugs)
6. [Best Practices](#best-practices)

---

## First Principles

> **Smooth playback = data is available when needed.**

Every design decision serves this single goal. Cache is not "store as much as possible"; it is "the target data must already be on disk when the player requests it."

---

## Architecture Overview

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
│  │  │libtorrent│  │ sparse  │  │  seek   │  │   GC    │ │   │
│  │  │ session │  │  file   │  │priority │  │(orphan) │ │   │
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

## Core Components

### 3.1 TorrentEngine

Responsibility: libtorrent session lifecycle, cache management, tiered eviction.

#### 3.1.1 Cache Tiers (L1/L2/L3/L4)

| Tier | Condition | Protection Level | Eviction Strategy |
|------|-----------|------------------|-------------------|
| **L1 (hot)** | Played within 24h | Highest | Soft limit does not evict; hard limit may evict |
| **L2 (warm)** | 100% complete + accessed within 7d | High | Not evicted at soft limit |
| **L3 (seed)** | 100% complete + cold (>7d) | Medium | Punch hole first (L3→L4), then evict |
| **L4 (fragment)** | Incomplete + cold | Low | Evict directly |

**Like protection**: Liked works receive +5000 in scoring, effectively creating an independent protection layer.

#### 3.1.2 Sliding Window Download Strategy

| Mode | Window | Description |
|------|--------|-------------|
| **Prefetch** | First 2% | Moov prioritized |
| **Play** | Moov only | Strict head-only; no window until progress reported |
| **Playing** | Current position ±30 pieces | ~2–4 minutes buffer |
| **Seek** | Target position ±15 pieces | Fast seek response |

Downloaded pieces are retained (priority=1); undownloaded pieces outside the window are set to 0. This prevents "blooming everywhere."

#### 3.1.3 Sparse File + Hole Detection

- Linux sparse file: undownloaded regions occupy no disk space
- `SEEK_HOLE` / `SEEK_DATA`: O(1) hole detection
- `FALLOC_FL_PUNCH_HOLE`: L3→L4 downgrade releases middle-piece disk space

#### 3.1.4 Cache Preload

On startup, `TorrentEngine._preload_cached_torrents()` scans `cache/torrent/` and auto-loads all existing `.torrent` files. This skips peer discovery and metadata download on first play.

---

### 3.2 PieceStateTracker

3×int bitmap state machine:

- `VERIFIED`: Confirmed by `SEEK_HOLE` or `libtorrent piece_finished_alert`
- `DOWNLOADING`: Priority/deadline set, waiting for peers
- `CORRUPT`: Hash failed or zero data read

O(1) `head_ready()`: Pre-computed moov mask + POPCNT.

See [piece-tracker.md](piece-tracker.md) for the full specification.

---

### 3.3 VideoStream

- **mmap read**: Avoids userspace copy
- **Piece-level hole detection**: `_detect_hole_offset` splits chunks at piece boundaries
- **Partial return**: If a hole is mid-chunk, return valid data before the hole so the player can keep decoding
- **Corrupt self-healing**: Verified piece reads zero → mark corrupt + trigger re-download

See [architecture.md](architecture.md) §4.2 for the read flow.

---

### 3.4 Cache Router

- `GET /api/cache`: List all cache items with actual data
- `GET /api/cache/metrics`: Statistics (completed / downloading / used / max)
- `POST /api/cache/gc-orphans`: Clean up disk orphans not in the database
- `DELETE /api/cache/{hash}`: Manual deletion

---

## Key Data Flows

### 4.1 Playback Flow

```
1. Frontend clicks play
   → POST /torrent/add (if not yet loaded)
   → GET  /api/check/{hash} (poll head_ready)

2. head_ready = true
   → GET /stream/{hash} (Range request)
   → stream_router calls read_video_range
   → seek_priority sets urgent pieces
   → _read_once reads via mmap
   → _detect_hole_offset checks for zero data
   → Returns 206 Partial Content

3. Player keeps requesting
   → update_play_progress slides window ±30 pieces
   → libtorrent downloads pieces inside the window
```

### 4.2 Cache Eviction Flow

```
1. add_torrent triggers _enforce_cache_limit
2. If used > soft_limit (95%)
   → Build candidate list (exclude hot + liked)
   → Sort by _cache_score (lowest first)
   → L3 → punch hole (keep head+tail)
   → L2/L4 → remove_torrent (delete files)
3. _periodic_clean repeats every 60s
```

---

## Fixed Bugs

### Bug 1: `_enforce_cache_limit` evicted only one torrent per cycle

**Symptom**: Cache far exceeded soft limit, but only one torrent was evicted per 60s cycle.

**Root cause**: Designed as "one per cycle," but batch-add scenarios were not considered.

**Fix**: Loop eviction until cache drops below soft limit.

### Bug 2: `_cleanup_orphaned` only ran at startup

**Symptom**: After `_readd_torrent` or manual deletion during runtime, old cache directories remained.

**Fix**: Integrated into `_periodic_clean`, executed every 60s.

### Bug 3: `remove_torrent` blocked thread for 0.5s

**Symptom**: Thread-pool workers were blocked by `sleep 0.5s`, reducing concurrency.

**Fix**: Reduced to 0.1s with retry loop.

### Bug 4: Hot torrents without like could be evicted

**Symptom**: Torrents played within 24h but not liked could be evicted at soft limit.

**Fix**: Soft limit protects all hot torrents (regardless of like). Like only affects warm/seed scoring.

---

## Best Practices

1. **Cache size = 60% of disk**: Leave 40% for the system and other services to avoid IO contention causing playback stutter.
2. **Moov first**: MP4 cannot play without the moov atom. Moov must download before any other data.
3. **Retain downloaded pieces**: When the window slides, old downloaded pieces keep priority=1. Do not discard them to avoid repeated downloads.
4. **Punch hole, do not delete**: L3 downgrade only removes middle pieces, keeping head+tail. Users re-playing do not need to re-download the head.
5. **Never return holes to the player**: The stream layer returns 416 or waits when zero data is detected. Zero data never reaches the decoder.
6. **One change, one commit**: Cache logic is sensitive. Change one thing at a time for easy rollback.
