# Bootstrap-First Verification

> Scope: `backend/services/torrent_engine.py` — `_on_metadata()`
> Goal: Replace minute-long hash recheck with a second-level `lseek` scan.

---

## Table of Contents

- [Problem](#problem)
- [Solution](#solution)
- [Code](#code)
- [Key Design](#key-design)
- [Results](#results)

---

## Problem

When a torrent restores from local cache (`params.ti = lt.torrent_info(path)`) and libtorrent reports `state=finished`:

**Old logic**: unconditional `force_recheck()` → 6 GB file hash verification → **5–15 minutes blocking**

During this period:
- `checking_files` state does not respond to `set_piece_deadline()`
- Frontend shows "checking" and cannot play
- Even if file data is intact, playback waits for the full recheck

---

## Solution

```
finished torrent
    │
    v
┌─────────────────────────────┐
│ tracker._bootstrap_from_    │  ← SEEK_HOLE lseek scan
│   filesystem()              │     completes in seconds
└─────────────┬───────────────┘
              │
    ├─ head_ready=True ──┐
    │                     v
    │          ┌──────────────────┐
    │          │ skip recheck     │
    │          │ info["ready"]=True
    │          │ return (seconds) │
    │          └──────────────────┘
    │
    └─ head_ready=False ──► log warning
                              let stream window re-download
                              (slow path, data actually missing)
```

---

## Code

```python
if not info.get("_recheck_done"):
    status = handle.status()
    if status.state == lt.torrent_status.finished:
        tracker = info.get("tracker")
        if tracker:
            tracker._bootstrap_from_filesystem()
            if tracker.head_ready():
                info["_recheck_done"] = True
                info["ready"] = True
                log.info(
                    f"bootstrap-first: {hash_str[:12]}... data intact, skip recheck"
                )
                return
            else:
                log.warning(
                    f"finished with holes: {hash_str[:12]}... "
                    f"disk scan shows missing data, will re-download"
                )
        # Do NOT force_recheck — it causes finished-state deadlock.
        # Let _set_stream_window set urgent priorities instead.
```

---

## Key Design

1. **lseek scan vs hash verification**
   - `lseek`: O(pieces), checks whether disk blocks are allocated → **seconds**
   - hash: O(bytes), computes SHA-1 per block → **minutes**

2. **Disk is the single source of truth**
   - `SEEK_HOLE` reports actual disk extents after `fsync()`
   - `have_piece()` is unreliable during `finished`/`checking` due to page-cache false positives
   - See [`piece-tracker.md`](piece-tracker.md) for bitmap state-machine details

3. **Run once per torrent session**
   - `_recheck_done` flag prevents repeated triggers
   - `_on_metadata` may be called multiple times (`add_torrent` existing path)

---

## Results

| Scenario | Old logic | New logic |
|----------|-----------|-----------|
| 6 GB file intact | 5–15 min recheck | **seconds lseek, instant ready** |
| 6 GB file missing tail | 5–15 min recheck + re-download | lseek detects gap → stream window re-downloads |
| 2 GB file intact | 2–5 min recheck | **instant ready** |

For "finished and data intact" caches (the most common case), startup drops from minutes to seconds.
