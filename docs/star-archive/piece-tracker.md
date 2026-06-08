# PieceStateTracker — Bitmap State Machine

> Scope: `backend/services/piece_tracker.py`
> Goal: O(1) piece-state queries, replacing Python-level loops

---

## Table of Contents

- [Core Design: Python int Bitmaps](#core-design-python-int-bitmaps)
- [State Machine](#state-machine)
- [O1 Queries](#o1-queries)
  - [head_ready](#head_ready)
  - [verified_count](#verified_count)
- [Batch Requests](#batch-requests)
- [Bootstrap SEEK_HOLE](#bootstrap-seek_hole)
- [Performance Comparison](#performance-comparison)

---

## Core Design: Python int Bitmaps

Three `int` values encode four states:

```python
_verified    = 0  # bit p = 1 → VERIFIED
_corrupt     = 0  # bit p = 1 → CORRUPT
_downloading = 0  # bit p = 1 → DOWNLOADING
# all 0 for a piece → NOT_DOWNLOADED
```

All bit operations (`& | ~ ^ << >> .bit_count()`) execute in C via `longobject.c`.
POPCNT (`.bit_count()`) maps directly to a single `POPCNT` CPU instruction.

---

## State Machine

```
NOT_DOWNLOADED ──► DOWNLOADING (request_pieces sets priority)
       │                  │
       │                  ▼ piece_finished_alert
       │            VERIFIED
       │                  │
       │                  ▼ hash_failed_alert
       │            CORRUPT ──► (auto-retry → DOWNLOADING)
       │
       └──► _bootstrap_from_filesystem (SEEK_HOLE scan)
```

---

## O(1) Queries

### head_ready()

```python
def head_ready(self) -> bool:
    if self._moov_pc == 0:
        return False
    return self._moov_vc == self._moov_pc   # single integer comparison
```

Pre-computed moov mask:

```python
def set_moov_range(self, moov_start: int, moov_end: int) -> None:
    sp = (self.file_offset + moov_start) // self.piece_length
    ep = (self.file_offset + moov_end) // self.piece_length
    self._moov_mask = ((1 << (ep - sp + 1)) - 1) << sp
    self._moov_pc = ep - sp + 1
    self._moov_vc = (self._verified & self._moov_mask).bit_count()
```

`_moov_vc` is maintained incrementally:
- `_set_verified(p)` → if `p` is inside moov range, `+= 1`
- `_set_corrupt(p)` → if `p` was verified and inside moov range, `-= 1`

### verified_count()

```python
def verified_count(self) -> int:
    return self._verified.bit_count()   # POPCNT instruction
```

---

## Batch Requests

```python
def request_pieces(self, start_piece: int, end_piece: int) -> int:
    start = max(start_piece, self.start_piece)
    end = min(end_piece, self.end_piece)
    if start > end:
        return 0

    mask = ((1 << (end - start + 1)) - 1) << start
    unavailable = (self._verified | self._downloading) & mask
    need = (mask & ~unavailable) >> start  # align LSB to piece 'start'

    pieces_to_set = []
    p = start
    temp = need
    while temp:
        if temp & 1:
            pieces_to_set.append(p)
        temp >>= 1
        p += 1

    if pieces_to_set:
        prios = list(self.handle.piece_priorities())
        for p in pieces_to_set:
            self.handle.set_piece_deadline(p, 0)
            if prios[p] != 7:
                prios[p] = 7
        self.handle.prioritize_pieces(prios)

    count = 0
    p = start
    while need:
        if need & 1:
            self._set_downloading(p)
            count += 1
        need >>= 1
        p += 1
    return count
```

**Key point**: use `prioritize_pieces(list)` batch API, not `piece_priority(p, 7)`.
The latter is silently ignored in Python bindings.

---

## Bootstrap (SEEK_HOLE)

```python
def _bootstrap_from_filesystem(self) -> None:
    if not os.path.exists(self.path):
        return

    # Flush page cache so SEEK_HOLE sees actual disk state
    try:
        fd = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass

    self._verified = 0
    self._corrupt = 0
    self._downloading = 0
    self._moov_vc = 0

    fd = os.open(self.path, os.O_RDONLY)
    try:
        offset = self.file_offset
        file_end = self.file_offset + self.video_size
        piece_len = self.piece_length

        while offset < file_end:
            piece = offset // piece_len
            piece_end = min((piece + 1) * piece_len, file_end)
            offset_in_file = offset - self.file_offset
            piece_end_in_file = piece_end - self.file_offset

            try:
                hole = os.lseek(fd, offset_in_file, os.SEEK_HOLE)
            except OSError:
                break

            if hole >= piece_end_in_file:
                self._verified |= (1 << piece)

            offset = piece_end
    finally:
        os.close(fd)
    self._moov_vc = (self._verified & self._moov_mask).bit_count()
```

**Critical detail**: the `lseek` offset must be the **relative offset inside the video file**, not the torrent absolute offset.
For example, ABF-328 has `file_offset=2,001,226`; using the absolute offset caused scan failures in earlier versions.

---

## Performance Comparison

| Operation | Old (list loop) | New (int bitmap) | Speedup |
|-----------|-----------------|------------------|---------|
| `head_ready` (5 pieces) | 1,378 ns | 111 ns | **12.4×** |
| `verified_count` (5,000 pieces) | 210,271 ns | 456 ns | **461×** |
| moov scan (per request) | 10.9 ms | 0.026 ms | **829×** |

See [`piece-tracker-optimization.md`](piece-tracker-optimization.md) for full benchmark methodology.
