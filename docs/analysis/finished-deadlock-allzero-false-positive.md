# Finished-State Deadlock & All-Zero False Positive

> **Keywords:** `finished`, `SEEK_HOLE`, sparse file, all-zero false positive, `_detect_hole_offset`, libtorrent mmap, PieceStateTracker

---

## Symptoms

Cache panel shows **100 % progress**, all piece segments green (state=VERIFIED), `head_ready=true`. Yet clicking **Play** keeps the video player in an endless loading spinner. Backend logs show `read_video_range` returning empty bytes after hitting a "hole".

Specific example: **SNOS-250** — progress 100 %, state `checking_files` after prior re-add, `verified_pieces=2703/2703`, but playback stalls.

---

## Investigation Timeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  Step 1: Verify torrent state                                        │
│  → get_status: progress=100, verified_pieces=2703/2703               │
│  → piece_segments all state=2 (VERIFIED)                             │
│  → head_ready=true                                                   │
│  → Ruled out "not enough data downloaded"                            │
├─────────────────────────────────────────────────────────────────────┤
│  Step 2: Trace /stream/ request path                                 │
│  → stream_router → read_video_range → _read_once → _detect_hole     │
│  → _detect_hole_offset returns 0 (hole at start of chunk)            │
│  → read_video_range waits 2s, retries, still hole, returns b""      │
│  → Browser gets 416 Range Not Satisfiable → stall                   │
├─────────────────────────────────────────────────────────────────────┤
│  Step 3: Analyze _detect_hole_offset logic                           │
│  → Checks:  if not any(overlap_data): treat as hole                  │
│  → "All zeros = hole" — but is this always true?                     │
│  → MP4 padding, H.264 skip macroblocks, alignment bytes can be zero   │
│  → Video data legitimately contains all-zero regions                 │
├─────────────────────────────────────────────────────────────────────┤
│  Step 4: Compare two hole-detection methods                          │
│  → _bootstrap_from_filesystem: uses SEEK_HOLE + fsync               │
│     → filesystem-allocation perspective, ignores content             │
│  → _detect_hole_offset: uses "not any(data)"                         │
│     → content perspective, misjudges zero-filled video data          │
│  → Same piece: verified by tracker, but _detect_hole says hole      │
│  → INCONSISTENCY → false positive                                    │
├─────────────────────────────────────────────────────────────────────┤
│  Step 5: Understand finished-state deadlock                          │
│  → When _detect_hole marks verified piece "corrupt"                  │
│  → It calls h.piece_priority(p, 7) + h.set_piece_deadline(p, 0)     │
│  → But libtorrent in finished state IGNORES piece_priority          │
│  → Re-download never starts → hole never fills → permanent stall    │
├─────────────────────────────────────────────────────────────────────┤
│  Step 6: Lock root cause                                             │
│  → _detect_hole_offset's all-zero check is the false-positive       │
│     trigger that converts a benign zero region into an unrecoverable │
│     finished-state deadlock.                                         │
│  → Secondary: _set_stream_window only readds when verified_count=0  │
│     missing the case where verified_count>0 but head_ready=false    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Root Cause Details

### 1. All-Zero Is NOT Equivalent to Hole

Video codecs (H.264, H.265, AV1) frequently produce zero-byte regions:

- **MP4 box padding**: `free` or `skip` boxes filled with `0x00`
- **NAL alignment**: Annex-B format inserts `00 00 00 01` start codes; between frames there can be large zero-padded areas
- **H.264 skip macroblocks**: Entire macroblocks encoded as "copy from reference"
- **File-system alignment**: Torrent clients pad the last piece to piece_length boundary

A 1 MiB–4 MiB piece containing a mix of video frames and padding can legitimately have an all-zero overlap region. `_detect_hole_offset` was flagging these as holes.

### 2. Two "Hole" Definitions Collided

| Component | Detection Method | Perspective | Can False-Positive? |
|---|---|---|---|
| `_bootstrap_from_filesystem` | `SEEK_HOLE` + `fsync` | Filesystem allocation (disk extents) | Rare (only on exotic CoW FS) |
| `_detect_hole_offset` | `not any(data)` | Content (byte values) | **Common** for video |

The system had **two independent definitions of "hole"** that did not agree. When they disagreed, the stream path (
`_detect_hole_offset`) overrode the bootstrap path, creating a deadlock.

### 3. Finished-State Deadlock Is Unrecoverable

libtorrent 2.0 with mmap storage:

1. Creates a sparse file of full torrent size on `add_torrent`
2. Downloads pieces via mmap
3. When all pieces report done → state becomes `finished`
4. In `finished` state, `piece_priority()` and `set_piece_deadline()` are **ignored**
5. Any hole that is detected after step 4 **cannot be filled by normal means**

The only recovery is `_readd_torrent()` (remove + clear resume data + re-add), which is rate-limited to once per 60 seconds.

### 4. `_set_stream_window` Had a Coverage Gap

```python
if status.state == lt.torrent_status.finished:
    if tracker and tracker.verified_count() == 0:
        self._readd_torrent(info["hash"])
```

This only triggered re-add when **zero** pieces were verified. But a far more common scenario is:

- Most pieces verified (via `_bootstrap_from_filesystem`)
- Moov atom range has a hole → `head_ready = False`
- `verified_count() > 0`, so re-add is **not triggered**
- Playback stalls because moov is incomplete, yet finished-state prevents repair

---

## Fix

### Fix 1: Trust Tracker State in `_detect_hole_offset`

**Core principle**: Tracker is the single source of truth.

```python
def _detect_hole_offset(path, start, data, engine, hash_str):
    # ...
    if tracker.is_verified(piece):
        # _bootstrap_from_filesystem used SEEK_HOLE + fsync.
        # Video padding/alignment can be all-zero — do NOT treat as hole.
        current = piece_end_in_file
        continue

    # Piece not verified: use SEEK_HOLE (filesystem perspective)
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            hole = os.lseek(fd, overlap_start, os.SEEK_HOLE)
            if hole < overlap_end:
                return data_start
        finally:
            os.close(fd)
    except OSError:
        # SEEK_HOLE unavailable: fallback to all-zero check
        if not any(overlap_data):
            return data_start
```

Key changes:
- **Verified pieces → unconditionally trusted**, regardless of byte content
- **Unverified pieces → `SEEK_HOLE` check**, avoiding content-based false positives
- All-zero fallback only when the OS does not support `SEEK_HOLE`

### Fix 2: Expand `_set_stream_window` Re-Add Trigger

```python
if status.state == lt.torrent_status.finished:
    tracker = info.get("tracker")
    if tracker:
        if tracker.verified_count() == 0 or not tracker.head_ready():
            self._readd_torrent(info["hash"])
            return False
```

Now re-add is triggered when **either**:
- No pieces are verified (original logic), **or**
- Moov is not ready (`head_ready = False`) — moov holes are also unrecoverable in finished state

---

## Lessons Learned

### One Truth, One Source

When multiple components define the same concept differently, they will eventually conflict. The system must designate **one component as the single source of truth** for each domain concept:

| Domain | Single Source of Truth |
|---|---|
| "Does this disk block contain data?" | `SEEK_HOLE` / `SEEK_DATA` (kernel) |
| "Is this piece safe to stream?" | `PieceStateTracker` (initialized from `SEEK_HOLE`) |

Content-level validation (`not any(data)`) must **never override** the tracker state.

### Video Data Breaks Text-File Assumptions

Binary video files are not like text logs or JSON. They contain:
- Large zero regions (padding)
- Highly compressible frames (dark scenes, fade-to-black)
- Alignment-induced artifacts at piece boundaries

Any stream or validation logic that assumes "all-zero = empty/missing" will eventually fail on real video content.

### Finished State Is a Trap Door

Once libtorrent enters `finished`, the piece-priority API becomes a no-op. Any bug that surfaces after this point is **unrecoverable by normal means**. Design rules:

1. Detect and fix inconsistencies **before** finished state is reached
2. If an inconsistency is found after finished, `_readd_torrent()` is the only exit
3. Rate-limit re-adds (60 s) to prevent infinite loops, but document the consequence: the user sees a stall until the rate limit expires

### First-Principle Debugging

When a symptom seems impossible ("100 % but can't play"), don't add workarounds. Trace the data flow:

```
Play click → waitForHeadReady → /api/check → find_video_state → head_ready
         ↓
    video.src = /stream/{hash}
         ↓
    /stream/ → read_video_range → _read_once → _detect_hole_offset
         ↓
    hole_offset == 0 → wait 2s → readd or return b""
         ↓
    Browser receives 416 → stall
```

At each arrow, ask: "What is the **exact** value returned? What invariant is violated?" The answer pointed directly to `_detect_hole_offset`'s all-zero assumption.

---

## References

- [libtorrent 2.0 mmap storage documentation](https://libtorrent.org/manual.html#storage)
- [Linux `lseek(2)` — `SEEK_HOLE` / `SEEK_DATA`](https://man7.org/linux/man-pages/man2/lseek.2.html)
- [Piece Tracker Design](../design/piece-tracker.md) — bitmap state machine
- [Bootstrap-First Verification](../design/bootstrap-first.md) — SEEK_HOLE scan rationale
- [Safari Code=4 Analysis](safari-code4.md) — related streaming consistency issue
