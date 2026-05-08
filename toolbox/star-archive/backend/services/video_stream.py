from __future__ import annotations

import os
import time
from typing import Any

import libtorrent as lt

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

    # If torrent is in finished state but some pieces in range are missing,
    # force a recheck to resume downloading. Otherwise libtorrent ignores
    # deadlines/priorities because it thinks everything is complete.
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
        if e.errno in (6, 22):  # ENXIO=6, EINVAL=22
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
    # 限制单次最大读取 1MB，避免浏览器发送 bytes=0- 时读取整个文件到内存
    MAX_CHUNK = 1024 * 1024
    chunk_size = min((end - start) + 1, MAX_CHUNK)

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

        # Hole detection: all-zero data may be legitimate (e.g. MP4 ftyp size field)
        # Use SEEK_DATA (filesystem-level) as primary check — works regardless of
        # libtorrent state (checking_files, finished, downloading).
        # Falls back to have_piece() only when SEEK_DATA is unavailable.
        hole = False
        if len(data) > 0 and not any(data):
            try:
                has_data = _is_data_at_offset(path, start)
                hole = not has_data
            except OSError:
                # SEEK_DATA not supported, fall back to libtorrent have_piece()
                with engine.lock:
                    info = engine.torrents.get(hash_str)
                if info:
                    h = info["handle"]
                    if h.status().has_metadata and info["video_idx"] is not None:
                        ti = h.torrent_file()
                        fs = ti.files()
                        if not _check_pieces_have(h, ti, fs, info["video_idx"], start, len(data)):
                            hole = True
                    else:
                        hole = True
                else:
                    hole = True

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
            break
        time.sleep(wait_step)
        elapsed += wait_step
        seek_priority(hash_str, start, end, engine)

    return bytes(data)
