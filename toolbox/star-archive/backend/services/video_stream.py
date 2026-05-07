from __future__ import annotations

import os
import time
from typing import Any

from .torrent_engine import find_video_state


def seek_priority(hash_str: str, start_byte: int, end_byte: int, engine: Any) -> None:
    """Set corresponding pieces to urgent based on Range request.

    只提升 Range 对应 piece 的 deadline，**不重置**其他 piece 的优先级，
    避免与 _set_stream_window 的 strict 策略冲突。
    """
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if not info:
        return
    h = info["handle"]
    if not h.status().has_metadata:
        return
    ti = h.torrent_file()
    fs = ti.files()
    idx = info["video_idx"]
    if idx is None:
        return
    piece_length = ti.piece_length()
    file_offset = fs.file_offset(idx)
    num_pieces = ti.num_pieces()

    start_piece = max(0, (file_offset + start_byte) // piece_length - 2)
    end_piece = min(num_pieces - 1, (file_offset + end_byte) // piece_length + 2)

    # Only set deadline for pieces in the Range, do NOT reset other priorities
    for p in range(start_piece, end_piece + 1):
        h.set_piece_deadline(p, 0)


def read_video_range(hash_str: str, start: int, end: int, engine: Any) -> bytes:
    """Read video data for a given byte range, stopping at holes.
    Returns the actual data read (may be less than requested if hole encountered).

    关键修复：
    1. 仅在 prefetch 模式首次 stream 时切换 play priority，不再每次请求重置。
    2. seek_priority 只提升 deadline，不覆盖已有优先级策略。
    3. 遇到 hole 时短暂等待（最多 2s），让 libtorrent 完成 urgent 下载。
    """
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if not path:
        return b""

    # Ensure play priority is applied ONLY when switching from prefetch mode
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        h = info["handle"]
        if h.status().has_metadata and info.get("prefetch"):
            info["prefetch"] = False
            engine._apply_play_priority(h, info)
        # Removed: do NOT call _apply_play_priority on every stream request,
        # because _apply_play_priority uses window_pcs=0 which resets the
        # sliding window to the file head, breaking seek/play progress.

    # Trigger urgent download for this range
    seek_priority(hash_str, start, end, engine)

    total_size = os.path.getsize(path)
    chunk_size = (end - start) + 1

    # Try reading; if we hit a hole, wait briefly for libtorrent to download
    max_wait = 2.0  # seconds
    wait_step = 0.2
    elapsed = 0.0

    while True:
        data = bytearray()
        hole = False
        with open(path, "rb") as f:
            f.seek(start)
            remaining = chunk_size
            while remaining > 0:
                buf = f.read(min(16384, remaining))
                if not buf:
                    break
                # Hole detection: treat a full zero chunk as a hole
                if len(buf) >= 16384 and not any(buf):
                    hole = True
                    break
                data.extend(buf)
                remaining -= len(buf)

        if not hole and len(data) > 0:
            return bytes(data)

        # Hole or empty — wait and retry if within max_wait
        if elapsed >= max_wait:
            break
        time.sleep(wait_step)
        elapsed += wait_step
        seek_priority(hash_str, start, end, engine)

    return bytes(data)
