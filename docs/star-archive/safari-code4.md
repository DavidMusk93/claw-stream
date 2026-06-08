# Safari code=4 Playback Failure Root Cause

> **Keywords:** `MEDIA_ERR_SRC_NOT_SUPPORTED`, libtorrent `checking_files`, sparse file, MP4 demuxer

---

## Symptoms

The video file is ~99 % downloaded (`real_size=3849969664/3849944387`). All `/stream/` requests return 206 with valid data, ffprobe confirms a valid MP4/H.264 file, and the moov atom is intact. Yet Safari reports `code=4` (`MEDIA_ERR_SRC_NOT_SUPPORTED`) and retries do not help.

Chrome plays the same file normally.

---

## Investigation Timeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  Step 1: Inspect backend responses                                   │
│  → 206 Partial Content, correct Content-Range, matching payload      │
│  → Ruled out HTTP protocol layer issues                              │
├─────────────────────────────────────────────────────────────────────┤
│  Step 2: ffprobe the file directly                                   │
│  → Valid MP4/H.264 + AAC, moov=[36, 7627023] intact                  │
│  → Ruled out file corruption                                         │
├─────────────────────────────────────────────────────────────────────┤
│  Step 3: Simulate Safari request pattern and concatenate             │
│  → Safari sends: 0-1, 0-3849944386, 3014656-..., 7602176-...         │
│  → Backend truncates to 1 MB chunks                                  │
│  → Concatenated output fails ffprobe with "contradictory STSC and    │
│    STCO"                                                             │
│  → Suspected range overlap / truncation causing concatenation issues │
├─────────────────────────────────────────────────────────────────────┤
│  Step 4: Re-evaluate concatenation                                   │
│  → Safari's second request is 0-3849944386 (entire file)             │
│  → Backend truncates to 0-1048575, but Safari may expect the full    │
│    file?                                                             │
│  → Ruled out: HTTP 206 explicitly allows truncation; Safari issues   │
│    follow-up ranges to fill gaps                                     │
├─────────────────────────────────────────────────────────────────────┤
│  Step 5: Inspect hole markers in logs                                │
│  → stream-router.log shows many hole=true entries                    │
│  → hole=true still returns 206 + data (log field does not affect     │
│    response)                                                         │
│  → Deep dive: hole detection uses "not any(data)", which misjudges   │
│    the first two bytes 00 00 of an MP4                               │
│  → This is a log red herring, not the root cause                     │
├─────────────────────────────────────────────────────────────────────┤
│  Step 6: Observe torrent status                                      │
│  → video-stream.log: state=checking_files                            │
│  → What does libtorrent do during checking_files?                    │
│  → Answer: temporarily zeroes / modifies pieces to verify hashes     │
├─────────────────────────────────────────────────────────────────────┤
│  Step 7: Lock root cause                                             │
│  → checking_files + read_video_range concurrent access               │
│  → read_video_range reads regions temporarily zeroed by libtorrent   │
│  → Returns all-zero data to Safari                                   │
│  → Safari MP4 demuxer hits an all-zero chunk → code=4                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Root Cause Details

### libtorrent `checking_files` Behavior

When a torrent is added to the session (especially when loading an existing file from the cache directory), libtorrent enters the `checking_files` state:

1. Reads every piece from the file.
2. Computes the piece hash.
3. Compares it with the hash in the torrent metadata.
4. **If the hash does not match, marks the piece as not downloaded (zeroes or deletes it).**

During this process the file content may be temporarily modified. If the HTTP streaming layer reads from the file at the same time, it can observe inconsistent all-zero data.

### Why Chrome Works and Safari Fails

Chrome's media parser is more tolerant of data inconsistency, or its retry logic differs. Safari's MP4 demuxer (based on AVFoundation) is strict: it aborts with `MEDIA_ERR_SRC_NOT_SUPPORTED` as soon as it encounters an all-zero chunk.

### Why `SEEK_HOLE` Did Not Catch It

`SEEK_HOLE` detects holes in a sparse file (unallocated disk blocks). libtorrent zeroes already-allocated blocks, so `SEEK_HOLE` reports "data present" even though the content is invalid.

---

## Fix

Core principle: **Do not stream while `checking_files` is in progress.**

### 1. `/api/check/{hash}` — Delay ready signal

```python
def check_stream(hash_str: str, engine: Any = Depends(get_engine)):
    local_path, local_size, head_ready_fs, mime = find_video_state(hash_str)
    # If the torrent is checking_files, report not ready even when the
    # filesystem has data.
    head_ready = head_ready_fs and not _is_torrent_checking(engine, hash_str)
    return StreamCheckResponse(head_ready=head_ready, ...)
```

### 2. `/stream/{hash}` — 503 rejection

```python
def stream_video(hash_str: str, request: Request, engine: Any = Depends(get_engine)):
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if _is_torrent_checking(engine, hash_str):
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "10"},
            detail="Torrent checking files"
        )
    # ... normal streaming
```

### 3. Frontend Behavior

The frontend `waitForHeadReady` polls `/api/check/`. When `head_ready=false` it keeps waiting. A 503 response from `/stream/` causes the browser to retry automatically (standard HTTP 503 + `Retry-After` behavior).

---

## Lessons Learned

### Do Not Assume "Data on Disk" Equals "Safe to Read"

A torrent client is not a static file server. Its internal states (`checking_files`, `downloading`, `seeding`) directly affect file consistency.

### Concurrent Access to Sparse Files Is Risky

The combination of sparse files and a dynamic download engine means "file exists" and "file is readable" are two distinct concepts. An additional state machine is required to coordinate them.

### Safari Is a Stricter Test Platform

Chrome may tolerate some data inconsistency, but Safari does not. If Safari can play the file, Chrome can too; the converse is not guaranteed. Use Safari as the compatibility baseline.

### Log Red Herrings

The `not any(data)` check misjudges MP4 files that start with `00 00`. This sent the investigation down the wrong path early on. Data-validation logic must be decoupled from file-format assumptions.

---

## References

- [libtorrent documentation — torrent_status](https://libtorrent.org/reference-Core.html#torrent-status)
- [Apple Developer — AVErrorMediaDiscontinuity](https://developer.apple.com/documentation/avfoundation/averror/averrormediadiscontinuity)
- [RFC 7233 — HTTP Range Requests](https://tools.ietf.org/html/rfc7233)

See also [Timeout Debug](timeout-debug.md) for upstream timeout issues and [Tracing and Logging](tracing-logging.md) for log analysis.
