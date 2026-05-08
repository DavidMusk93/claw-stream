from __future__ import annotations

import os
import time
from typing import Any

from core import get_logger
from .torrent_engine import find_video_state

log = get_logger("video-stream")


def seek_priority(hash_str: str, start_byte: int, end_byte: int, engine: Any) -> None:
    """Set corresponding pieces to urgent based on Range request.

    只提升 Range 对应 piece 的 deadline，**不重置**其他 piece 的优先级，
    避免与 _set_stream_window 的 strict 策略冲突。
    """
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if not info:
        log.debug("seek_priority: torrent not found", extra={"hash": hash_str[:12]})
        return
    h = info["handle"]
    if not h.status().has_metadata:
        log.debug("seek_priority: no metadata yet", extra={"hash": hash_str[:12]})
        return
    ti = h.torrent_file()
    fs = ti.files()
    idx = info["video_idx"]
    if idx is None:
        log.debug("seek_priority: video_idx is None", extra={"hash": hash_str[:12]})
        return
    piece_length = ti.piece_length()
    file_offset = fs.file_offset(idx)
    num_pieces = ti.num_pieces()

    start_piece = max(0, (file_offset + start_byte) // piece_length - 2)
    end_piece = min(num_pieces - 1, (file_offset + end_byte) // piece_length + 2)

    # Set both deadline and priority so libtorrent resumes from 'finished'
    # state immediately and downloads the piece with full speed.
    deadline_count = 0
    prio_count = 0
    for p in range(start_piece, end_piece + 1):
        h.set_piece_deadline(p, 0)
        deadline_count += 1
        old_prio = h.piece_priority(p)
        if old_prio != 7:
            h.piece_priority(p, 7)
            prio_count += 1
    log.debug(
        "seek_priority",
        extra={
            "hash": hash_str[:12],
            "start_byte": start_byte,
            "end_byte": end_byte,
            "start_piece": start_piece,
            "end_piece": end_piece,
            "deadline_count": deadline_count,
            "prio_changed": prio_count,
            "state": str(h.status().state),
        },
    )


def read_video_range(hash_str: str, start: int, end: int, engine: Any) -> bytes:
    """Read video data for a given byte range, stopping at holes.
    Returns the actual data read (may be less than requested if hole encountered).
    """
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if not path:
        log.debug("read_video_range: video not found", extra={"hash": hash_str[:12], "start": start, "end": end})
        return b""

    # Ensure play priority is applied ONLY when switching from prefetch mode
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        h = info["handle"]
        if h.status().has_metadata and info.get("prefetch"):
            info["prefetch"] = False
            engine._apply_play_priority(h, info)
            log.debug("read_video_range: switched prefetch→play", extra={"hash": hash_str[:12]})

    # Trigger urgent download for this range
    seek_priority(hash_str, start, end, engine)

    total_size = os.path.getsize(path)
    chunk_size = (end - start) + 1

    max_wait = 15.0
    wait_step = 0.5
    elapsed = 0.0
    attempt = 0

    while True:
        attempt += 1
        data = bytearray()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = chunk_size
            while remaining > 0:
                buf = f.read(min(16384, remaining))
                if not buf:
                    break
                data.extend(buf)
                remaining -= len(buf)

        hole = len(data) > 0 and not any(data)

        log.debug(
            "read_video_range attempt",
            extra={
                "hash": hash_str[:12],
                "attempt": attempt,
                "start": start,
                "end": end,
                "requested": chunk_size,
                "read": len(data),
                "hole": hole,
                "elapsed": round(elapsed, 1),
                "head_ready": head_ready,
                "real_size": real_size,
            },
        )

        if not hole and len(data) > 0:
            log.debug(
                "read_video_range success",
                extra={"hash": hash_str[:12], "attempt": attempt, "read": len(data), "elapsed": round(elapsed, 1)},
            )
            return bytes(data)

        if elapsed >= max_wait:
            log.warning(
                "read_video_range hole timeout",
                extra={
                    "hash": hash_str[:12],
                    "start": start,
                    "end": end,
                    "read": len(data),
                    "elapsed": round(elapsed, 1),
                    "head_ready": head_ready,
                    "real_size": real_size,
                },
            )
            break
        time.sleep(wait_step)
        elapsed += wait_step
        seek_priority(hash_str, start, end, engine)

    return bytes(data)
