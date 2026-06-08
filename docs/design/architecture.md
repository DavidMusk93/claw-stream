# claw-stream System Architecture

> Project: `claw-stream`  
> Backend: FastAPI + libtorrent 2.0.11  
> Goal: On-demand download, tiered cache, seamless playback.

---

## Table of Contents

1. [Service Architecture](#1-service-architecture)
2. [Playback Flow](#2-playback-flow)
   - [2.1 Full Interaction](#21-full-interaction)
   - [2.2 Engine State Machine](#22-engine-state-machine)
   - [2.3 Prefetch vs. Play Mode](#23-prefetch-vs-play-mode)
3. [Key Subsystems](#3-key-subsystems)
   - [3.1 PieceStateTracker](#31-piecestatetracker)
   - [3.2 Bootstrap-first Verification](#32-bootstrap-first-verification)
   - [3.3 Tiered Cache](#33-tiered-cache)
   - [3.4 Cache Preload](#34-cache-preload)
   - [3.5 GC Touch Protection](#35-gc-touch-protection)
4. [Sparse Files and Hole Detection](#4-sparse-files-and-hole-detection)
   - [4.1 SEEK_DATA / SEEK_HOLE](#41-seek_data--seek_hole)
   - [4.2 Stream Read Flow](#42-stream-read-flow)
5. [Troubleshooting Cheat Sheet](#5-troubleshooting-cheat-sheet)
6. [Monitoring Endpoints](#6-monitoring-endpoints)

---

## 1. Service Architecture

```
User Browser
    │
    │ HTTPS :443
    v
┌─────────────────────────────────────┐
│ Caddy (reverse proxy)               │
│ - Auto TLS (Let's Encrypt)          │
│ - Auto certificate renewal          │
└─────────────────────────────────────┘
    │
    ├─ / ────────────────► Nuxt frontend (:3000)
    ├─ /api/* ───────────► FastAPI backend (:8765)
    ├─ /stream/* ────────► FastAPI backend (:8765)
    └─ /torrent/* ───────► FastAPI backend (:8765)
```

---

## 2. Playback Flow

### 2.1 Full Interaction

```
User clicks play
    │
    v
GET /api/check/<hash>  ──►  check_stream()
    │                           - find_video_state() scans file
    │                           - If checking_files → head_ready=false
    │
    ├─ head_ready = true ──► video.src = /stream/<hash>
    │                           - stream_video() reads Range
    │                           - seek_priority() triggers urgent download
    │                           - Returns 206 Partial Content
    │
    └─ head_ready = false ─► POST /torrent/add
                              - add_torrent() adds magnet
                              - _on_metadata() selects video file
                              - Polls /torrent/status (1s interval)
                              - Plays after head_ready
```

### 2.2 Engine State Machine

```
add_torrent(magnet)
    │
    v
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│ metadata_wait │────►│ checking_files│────►│ downloading   │
│ (DHT/tracker) │     │ (hash verify) │     │ (priority=0)  │
└───────────────┘     └───────────────┘     └───────────────┘
       │                                           │
       │                                           v
       │                                    bootstrap-first
       │                                    (finished → skip recheck)
       │                                           │
       v                                           v
metadata_received_alert                    cache preload
       │                                    (on startup)
       v
_on_metadata()
- Pick hhd800.com HD video file
- Scan / cache moov range
- Set file priority=4
- Strict on-demand: all piece prio=0
```

### 2.3 Prefetch vs. Play Mode

| Aspect | Prefetch | Play Mode |
|--------|----------|-----------|
| Trigger | Auto after page load | User clicks play |
| Strategy | First 2% pieces: prio=4 | Moov urgent (prio=7), others=0 |
| Rest | prio=0 | prio=0 |
| Goal | Green badge on play button | Moov ready → instant playback |
| Disk | ~100–200 MB per title | ~60 MB head (moov) |

---

## 3. Key Subsystems

### 3.1 PieceStateTracker

Three Python `int` bitmaps encode four states:

```python
_verified    = 0  # bit p = 1 → VERIFIED
_corrupt     = 0  # bit p = 1 → CORRUPT
_downloading = 0  # bit p = 1 → DOWNLOADING
# all 0 → NOT_DOWNLOADED
```

- `head_ready()`: `_moov_vc == _moov_pc` → O(1) integer comparison
- `verified_count()`: `_verified.bit_count()` → O(1) POPCNT
- `request_pieces()`: Bitmask filtering + batch `prioritize_pieces()`

See [piece-tracker.md](piece-tracker.md) for details.

### 3.2 Bootstrap-first Verification

For finished torrents, `SEEK_HOLE` lseek scan verifies disk state. If `head_ready=True`, skip `force_recheck()`.

**Before**: Unconditional recheck → 5–15 min blocking.  
**After**: Data intact → ready in seconds; only missing data falls back to recheck.

See [bootstrap-first.md](bootstrap-first.md).

### 3.3 Tiered Cache

```
L1 hot:      Played within 24h  → never evict at soft limit
L2 warm:     100% complete + accessed within 7d → high retention
L3 seed:     100% complete + cold (>7d) → punch hole eligible
L4 fragment: Incomplete + cold → evict first
```

Eviction uses `_cache_score()` instead of pure LRU:

```python
score = (play_bonus + completion) / size_gb * heat_decay + play_bonus
```

See [tiered-cache.md](tiered-cache.md).

### 3.4 Cache Preload

On startup, `TorrentEngine` scans `cache/torrent/` and auto-loads all cached `.torrent` files via `add_torrent()`. This eliminates the peer-discovery wait on first play.

### 3.5 GC Touch Protection

`/stream/{hash}` and `/api/check/{hash}` call `engine.touch(hash_str)` on every request:

- Updates `last_access`
- Prevents actively streamed torrents from eviction

`_last_play_time` and `_play_count` are only updated by `/stream/{hash}` (not `/api/check`), preventing every checked torrent from being promoted to L1 (hot).

---

## 4. Sparse Files and Hole Detection

### 4.1 SEEK_DATA / SEEK_HOLE

```python
# Check if offset has real data (not a sparse hole)
os.lseek(fd, offset, os.SEEK_DATA) == offset

# Check if [start, end] range contains a hole
os.lseek(fd, start, os.SEEK_HOLE) >= end + 1
```

More reliable than `not any(data)` zero-detection:

- libtorrent temporarily zeros pieces during `checking_files`
- MP4 `ftyp` headers contain `00 00` bytes, causing false positives with zero-detection

### 4.2 Stream Read Flow

```
read_video_range(start, end)
    │
    ├─► seek_priority(start, end)  # set urgency
    │
    ├─► _read_once(path, start, chunk_size)
    │   └─► mmap read (fallback to buffered read)
    │
    ├─► _detect_hole_offset() → piece-level zero check
    │   └─► If tracker says VERIFIED but data is zero → mark CORRUPT + re-download
    │
    └─► If hole: wait 0.1s, retry (max 2s)
            ├─► libtorrent downloads the piece
            └─► Re-read → data present → return
```

If a hole is at the start of the chunk and the 2s timeout expires, return empty bytes → caller returns **416** (not 200/206 with zero data).

If a hole is mid-chunk, return the valid prefix before the hole → player can keep decoding.

---

## 5. Troubleshooting Cheat Sheet

| Symptom | Check | Solution |
|---------|-------|----------|
| Click play, no response | `curl /api/check/<hash>` → head_ready? | If false, wait 30–60s or check peers |
| Black screen / stutter during playback | Check `state=checking_files` | Wait for checking to complete (503 auto-retry) |
| Seek hangs | Check `video-stream.log` for hole timeout | Normal — libtorrent is urgent-downloading |
| 100% progress but unplayable | Moov is at tail (non-faststart) | These files cannot stream while downloading |
| Disk fills up instantly | Check piece priorities | Only moov + window = 7, others = 0 |
| Backend 502 | `journalctl -u caddy-claw` | Check upstream timeout |

### 5.1 Common Commands

```bash
# Check torrent status
curl -s http://localhost:8765/torrent/status/<hash> | python3 -m json.tool

# Check sparse file real size
stat --format="logical=%s actual=%b*%B=%B" cache/torrent/<hash>/.../*.mp4

# View backend logs
journalctl -u star-archive-backend -f

# Check piece priorities (debug)
python3 -c "
import libtorrent as lt
s = lt.session()
# ...
"
```

---

## 6. Monitoring Endpoints

```bash
GET /api/health              → {"status": "ok"}
GET /api/cache               → Cache list + total size
GET /api/cache/metrics       → Completed / downloading / used / limit
GET /torrent/status/<hash>   → Full torrent status (including tier)
GET /api/check/<hash>        → head_ready / cached / mime
```
