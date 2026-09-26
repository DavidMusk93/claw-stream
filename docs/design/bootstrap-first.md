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
    if status.state in (lt.torrent_status.finished, lt.torrent_status.seeding):
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
                    f"disk scan shows missing data, forcing recheck"
                )
                self._force_recheck(hash_str, info)
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

---

## Fast-Resume Snapshots (restart path)

Bootstrap-first only helps once libtorrent knows the piece states. A plain
re-add of a paused torrent with existing files lands in `checking_files`
limbo: the check is deferred while paused, and the first play after a restart
pays a **full-file hash check** (measured: ~25s for a 6.1GB sparse file)
before any piece can download.

To eliminate this, the engine persists libtorrent resume data
(`{hash}.resume` next to `{hash}.torrent`):

- **Save**: on `pause_download` (cache-keeping path) and on graceful
  `shutdown()` (synchronous flush of all non-checking torrents).
- **Load**: `_add_torrent_inner` merges the saved `have_pieces` bitmask into
  the add params (`_load_resume_data`). The snapshot's info-hash must match;
  only the piece bitmask is taken — flags, save_path, and the on-demand
  priority discipline stay under engine control.
- **Invalidate**: punch-hole and stale-metadata paths delete the snapshot —
  libtorrent 2.0 trusts `have_pieces` blindly (verified experimentally: a
  corrupted file still reports seeding), so the file must never be older
  than the data it describes. The disk-truth tracker (SEEK_HOLE bootstrap)
  remains the safety net: if the snapshot overstates disk contents,
  bootstrap-first detects the holes and forces one real recheck.
- **Migration**: at preload, torrents without a snapshot are briefly resumed
  (all priorities are 0, so nothing downloads) purely to run the one-time
  check; `torrent_checked_alert` then snapshots them and re-pauses them if
  the user hasn't started playing.

While a check *is* running (first-ever add, crash recovery), the UI shows
real progress: `check_progress` (libtorrent's native `status().progress`)
is exposed via REST and SSE `torrent.progress`.
