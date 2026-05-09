from __future__ import annotations

import asyncio
import errno
import mmap
import os
import time
from typing import Any

import libtorrent as lt

from core import get_logger
from .torrent_engine import find_video_state

log = get_logger("video-stream")


def seek_priority(hash_str: str, start_byte: int, end_byte: int, engine: Any) -> None:
    """Set corresponding pieces to urgent based on Range request.

    Uses PieceStateTracker to avoid requesting already-verified pieces,
    and avoids finished-state deadlock by trusting tracker state over
    libtorrent's potentially stale have_piece bitmap.
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

    # Use tracker if available — avoids finished-state deadlock
    tracker = info.get("tracker")
    if tracker:
        requested = tracker.request_pieces(start_piece, end_piece)
        log.debug(
            "seek_priority via tracker",
            extra={
                "hash": hash_str[:12],
                "start_byte": start_byte,
                "end_byte": end_byte,
                "start_piece": start_piece,
                "end_piece": end_piece,
                "requested": requested,
                "state": str(h.status().state),
            },
        )
        return

    # Fallback: direct libtorrent manipulation (old behaviour)
    deadline_count = 0
    prio_count = 0
    for p in range(start_piece, end_piece + 1):
        h.set_piece_deadline(p, 0)
        deadline_count += 1
        old_prio = h.piece_priority(p)
        if old_prio != 7:
            h.piece_priority(p, 7)
            prio_count += 1

    status = h.status()
    state = status.state
    forced_recheck = False
    if state == lt.torrent_status.finished:
        missing = any(not h.have_piece(p) for p in range(start_piece, end_piece + 1))
        if missing:
            h.force_recheck()
            forced_recheck = True

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
            "state": str(state),
            "forced_recheck": forced_recheck,
        },
    )


def _is_data_at_offset(path: str, offset: int) -> bool:
    """使用 Linux SEEK_DATA 检查文件 offset 处是否有实际数据（非 sparse hole）。

    比 libtorrent have_piece() 更可靠，不受 checking_files / finished 状态影响。
    如果文件系统不支持 SEEK_DATA，回退到 have_piece()（通过调用方处理）。
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        data_offset = os.lseek(fd, offset, os.SEEK_DATA)
        # offset 本身有数据，或者 SEEK_DATA 跳到了后面的数据区域
        return data_offset == offset
    except OSError as e:
        # ENXIO = 没有更多数据；EINVAL = 不支持 SEEK_DATA
        if e.errno in (errno.ENXIO, errno.EINVAL):
            return False
        raise
    finally:
        os.close(fd)


def _check_pieces_have(h: Any, ti: Any, fs: Any, idx: int, start_byte: int, data_len: int) -> bool:
    """检查 [start_byte, start_byte+data_len) 范围涉及的所有 piece 是否已下载。

    回退方案：当 SEEK_DATA 不可用时使用 libtorrent have_piece()。
    注意：checking_files 状态下 have_piece() 不可靠（全返回 False）。
    """
    piece_length = ti.piece_length()
    file_offset = fs.file_offset(idx)
    abs_start = file_offset + start_byte
    abs_end = abs_start + data_len - 1
    start_piece = abs_start // piece_length
    end_piece = abs_end // piece_length
    num_pieces = ti.num_pieces()
    for p in range(start_piece, min(end_piece + 1, num_pieces)):
        if not h.have_piece(p):
            return False
    return True


def _read_once(path: str, start: int, chunk_size: int) -> bytearray:
    """Read [start, start+chunk_size) via mmap. Returns bytearray."""
    try:
        with open(path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return bytearray(mm[start:start + chunk_size])
    except (OSError, ValueError):
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
        return data


def _detect_hole(path: str, start: int, data: bytearray, engine: Any, hash_str: str) -> bool:
    """Check if data is actually a hole. Returns True if hole detected."""
    if len(data) == 0:
        return True
    if not any(data):
        try:
            return not _is_data_at_offset(path, start)
        except OSError:
            with engine.lock:
                info = engine.torrents.get(hash_str)
            if info:
                h = info["handle"]
                if h.status().has_metadata and info["video_idx"] is not None:
                    ti = h.torrent_file()
                    fs = ti.files()
                    return not _check_pieces_have(h, ti, fs, info["video_idx"], start, len(data))
            return True
    return False


async def read_video_range(hash_str: str, start: int, end: int, engine: Any) -> bytes:
    """Read video data for a given byte range, stopping at holes.
    Returns the actual data read (may be less than requested if hole encountered).
    """
    path, real_size, head_ready, mime = await asyncio.to_thread(find_video_state, hash_str)
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
            await asyncio.to_thread(engine._apply_play_priority, h, info)
            log.debug("read_video_range: switched prefetch→play", extra={"hash": hash_str[:12]})

    # Trigger urgent download for this range
    await asyncio.to_thread(seek_priority, hash_str, start, end, engine)

    # If torrent not in engine, try to re-add from local cache so that
    # seek_priority can set piece priorities for future requests.
    with engine.lock:
        info = engine.torrents.get(hash_str)
    if not info:
        magnet = f"magnet:?xt=urn:btih:{hash_str}"
        try:
            await asyncio.to_thread(engine.add_torrent, magnet, prefetch=False)
            log.debug("read_video_range: auto-added torrent", extra={"hash": hash_str[:12]})
        except Exception as e:
            log.debug("read_video_range: auto-add failed", extra={"hash": hash_str[:12], "error": str(e)})

    total_size = await asyncio.to_thread(os.path.getsize, path)
    # 限制单次最大读取 8MB。Safari needs enough data to parse moov + first frames.
    # 1MB truncates range responses too aggressively, causing Safari demuxer issues.
    MAX_CHUNK = 8 * 1024 * 1024
    chunk_size = min((end - start) + 1, MAX_CHUNK)

    # Short timeout: if data isn't here yet, return empty and let the client retry
    # (Safari auto-retries 206/416). We use asyncio.sleep so we don't block
    # thread-pool workers.
    max_wait = 2.0
    wait_step = 0.1
    elapsed = 0.0
    attempt = 0

    while True:
        attempt += 1
        data = await asyncio.to_thread(_read_once, path, start, chunk_size)
        hole = await asyncio.to_thread(_detect_hole, path, start, data, engine, hash_str)

        log.debug(
            "read_video_range attempt",
            extra={
                "hash": hash_str[:12],
                "attempt": attempt,
                "start": start,
                "end": start + len(data) - 1 if data else end,
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
            # Return empty bytes so caller can send 416 instead of all-zero data
            return b""
        await asyncio.sleep(wait_step)
        elapsed += wait_step
        await asyncio.to_thread(seek_priority, hash_str, start, end, engine)

    return bytes(data)
