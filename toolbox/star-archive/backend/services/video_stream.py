from __future__ import annotations

import os
import re
from typing import Any

from .torrent_engine import find_video_state, CACHE_DIR


def seek_priority(hash_str: str, start_byte: int, end_byte: int, engine: Any) -> None:
    """Set corresponding pieces to urgent based on Range request."""
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

    prios = [1] * num_pieces
    for p in range(start_piece, end_piece + 1):
        prios[p] = 7
        h.set_piece_deadline(p, 0)
    h.prioritize_pieces(prios)


def read_video_range(hash_str: str, start: int, end: int, engine: Any) -> bytes:
    """Read video data for a given byte range, stopping at holes.
    Returns the actual data read (may be less than requested if hole encountered).
    """
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if not path:
        return b""

    # Ensure play priority is applied
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        h = info["handle"]
        if h.status().has_metadata and info.get("prefetch"):
            info["prefetch"] = False
            engine._apply_play_priority(h, info)
        elif h.status().has_metadata:
            engine._apply_play_priority(h, info)

    # Trigger urgent download for this range
    seek_priority(hash_str, start, end, engine)

    total_size = os.path.getsize(path)
    chunk_size = (end - start) + 1

    data = bytearray()
    with open(path, "rb") as f:
        f.seek(start)
        remaining = chunk_size
        while remaining > 0:
            buf = f.read(min(16384, remaining))
            if not buf:
                break
            # Hole detection: only treat as hole if we read a full chunk (>=16384)
            if len(buf) >= 16384 and not any(buf):
                break
            data.extend(buf)
            remaining -= len(buf)

    return bytes(data)


def read_video_full(hash_str: str, engine: Any) -> bytes:
    """Read video from start, stopping at first hole."""
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if not path:
        return b""

    with engine.lock:
        info = engine.torrents.get(hash_str)
    if info:
        h = info["handle"]
        if h.status().has_metadata and info.get("prefetch"):
            info["prefetch"] = False
            engine._apply_play_priority(h, info)
        elif h.status().has_metadata:
            engine._apply_play_priority(h, info)

    data = bytearray()
    with open(path, "rb") as f:
        while True:
            buf = f.read(16384)
            if not buf:
                break
            if len(buf) >= 16384 and not any(buf):
                break
            data.extend(buf)

    return bytes(data)
