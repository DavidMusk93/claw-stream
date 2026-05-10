from __future__ import annotations

import math
import os
import re
import shutil
import threading
import time
from typing import Any

import libtorrent as lt

from core import get_logger
from .piece_tracker import PieceStateTracker

log = get_logger("torrent-engine")

# ── Config ──────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_SCRIPT_DIR, "cache", "torrent")

MAX_CACHE_SIZE_GB = 15
PREFETCH_COUNT = 13
PREFETCH_PERCENT = 0.02
CACHE_CLEAN_INTERVAL_SEC = 60  # 后台清理间隔
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".webm"}
SPAM_PATTERNS = [re.compile(p, re.I) for p in [
    r"game pack", r"996gg", r"^\d+\.txt$", r"^readme", r"\.url$", r"\.txt$"
]]

# 常见番号格式匹配器
_WORK_CODE_RE = re.compile(r"[A-Z]{2,6}-\d{3,5}", re.I)

# Cache MP4 moov scan results: path -> (moov_start, moov_end).
# Moov position never changes for a given file, so caching is safe.
# We only cache successful scans (moov_end > 0) to avoid caching
# tail-moov files that aren't fully downloaded yet.
_MOOV_CACHE: dict[str, tuple[int, int]] = {}


def _extract_work_code(name: str) -> str | None:
    """从文件名或 torrent 名中提取作品番号（如 ABC-123）。"""
    if not name:
        return None
    m = _WORK_CODE_RE.search(name)
    return m.group(0).upper() if m else None

os.makedirs(CACHE_DIR, exist_ok=True)


def _scan_mp4_moov(path: str, max_read: int = 16 * 1024 * 1024) -> tuple[int, int]:
    """扫描 MP4 文件，查找 moov box 的起始和结束位置。

    返回 (moov_start, moov_end)。moov_start=0 且 moov_end>0 表示 head-moov。
    moov_start>0 表示 tail-moov。返回 (0, 0) 表示未找到。
    """
    cached = _MOOV_CACHE.get(path)
    if cached is not None:
        return cached
    # Note: we intentionally do NOT cache (0, 0) "not found" results.
    # A file may be partially downloaded when first scanned; once more data
    # arrives the moov atom becomes visible and must be re-scanned.

    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(max_read)
            offset = 0
            mdat_end = 0
            while offset < len(data) - 8:
                size = int.from_bytes(data[offset:offset+4], "big")
                box_type = data[offset+4:offset+8]
                if size == 0:
                    mdat_end = file_size
                    break
                if size == 1:
                    if offset + 16 > len(data):
                        break
                    size = int.from_bytes(data[offset+8:offset+16], "big")
                    if size > 100 * 1024 * 1024 * 1024:
                        break
                    if box_type == b"mdat":
                        mdat_end = offset + size
                        break
                elif size < 8 or size > 100 * 1024 * 1024 * 1024:
                    break
                elif box_type == b"mdat":
                    mdat_end = offset + size
                    break
                if box_type == b"moov":
                    result = (0, offset + size)
                    _MOOV_CACHE[path] = result
                    return result
                offset += size

            # tail-moov: moov is after mdat
            if mdat_end > 0:
                f.seek(max(0, mdat_end - 1024))
                check = f.read(2048)
                moov_idx = check.find(b"moov")
                if moov_idx >= 4:
                    box_size_raw = int.from_bytes(check[moov_idx - 4:moov_idx], "big")
                    if box_size_raw == 1 and moov_idx >= 12:
                        box_size = int.from_bytes(check[moov_idx - 12:moov_idx - 4], "big")
                    else:
                        box_size = box_size_raw
                    if 0 < box_size < 100 * 1024 * 1024:
                        moov_start = (mdat_end - 1024 if mdat_end >= 1024 else 0) + moov_idx - 4
                        result = (moov_start, moov_start + box_size)
                        _MOOV_CACHE[path] = result
                        return result
    except Exception:
        pass
    return 0, 0


def _mime_type(path: str) -> str:
    """根据文件扩展名返回对应的 MIME 类型。"""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
        ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
    }.get(ext, "video/mp4")


def _range_has_data(path: str, start: int, end: int) -> bool:
    """使用 Linux SEEK_HOLE 确认 [start, end] 范围内没有 sparse hole。

    原理：SEEK_HOLE(start) 返回 >= start 的第一个 hole 偏移。
    如果该偏移 > end，说明 [start, end] 全是实际数据。
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        hole_offset = os.lseek(fd, start, os.SEEK_HOLE)
        return hole_offset > end
    except OSError as e:
        # EINVAL = 不支持 SEEK_HOLE；ENXIO = start 之后没有 hole（全是数据）
        if e.errno == 22:  # EINVAL
            return False
        if e.errno == 6:   # ENXIO — no more holes, all data
            return True
        raise
    finally:
        os.close(fd)


def _check_video_ready(path: str, hash_str: str = "") -> tuple[int, bool, str]:
    """Check if a known video file is ready for playback.

    Returns (real_size, head_ready, mime).
    """
    try:
        st = os.stat(path)
    except OSError:
        return 0, False, "video/mp4"

    real_size = st.st_blocks * 512
    logic_size = st.st_size
    if real_size < 1024 * 1024:
        return real_size, False, _mime_type(path)

    mime = _mime_type(path)
    ext = os.path.splitext(path)[1].lower()

    # MP4/MOV: need moov atom downloaded (entire moov range, no holes)
    if ext in (".mp4", ".m4v", ".mov"):
        moov_start, moov_end = _scan_mp4_moov(path)
        if moov_end == 0:
            # moov not in head, try find in tail (scan last 128MB)
            try:
                tail_scan_size = min(128 * 1024 * 1024, logic_size)
                tail_offset = max(0, logic_size - tail_scan_size)
                with open(path, "rb") as f:
                    f.seek(tail_offset)
                    tail = f.read(tail_scan_size)
                    moov_idx = tail.find(b"moov")
                    if moov_idx >= 4:
                        box_size_raw = int.from_bytes(tail[moov_idx - 4:moov_idx], "big")
                        if box_size_raw == 1 and moov_idx >= 12:
                            box_size = int.from_bytes(tail[moov_idx - 12:moov_idx - 4], "big")
                        else:
                            box_size = box_size_raw
                        if 0 < box_size < 100 * 1024 * 1024:
                            moov_start = tail_offset + moov_idx - 4
                            moov_end = moov_start + box_size
                        else:
                            return real_size, False, mime
                    else:
                        return real_size, False, mime
            except Exception:
                return real_size, False, mime

        head_ready = False
        try:
            if _range_has_data(path, moov_start, moov_end - 1):
                head_ready = True
            else:
                if hash_str:
                    log.debug(
                        "find_video_state: moov has hole",
                        extra={
                            "hash": hash_str[:12],
                            "moov_start": moov_start,
                            "moov_end": moov_end,
                            "tail_moov": moov_start > 0,
                        },
                    )
        except Exception:
            pass
        return real_size, head_ready, mime

    # MKV/WEBM: need first 5MB downloaded for header parsing
    if ext in (".mkv", ".webm"):
        return real_size, real_size >= 5 * 1024 * 1024, mime

    # Other formats: need first 10MB
    return real_size, real_size >= 10 * 1024 * 1024, mime


def find_video_state(hash_str: str) -> tuple[str | None, int, bool, str]:
    """查找视频文件并检查是否已下载足够的头部数据以供播放。

    返回: (文件路径, 实际磁盘大小, 头部是否就绪, MIME 类型)
    """
    dir_path = os.path.join(CACHE_DIR, hash_str)
    if not os.path.exists(dir_path):
        return None, 0, False, "video/mp4"
    best = None
    best_size = 0
    for root, dirs, files in os.walk(dir_path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            fp = os.path.join(root, f)
            try:
                st = os.stat(fp)
                real_size = st.st_blocks * 512
                if real_size > best_size:
                    best_size = real_size
                    best = fp
            except OSError:
                pass
    if not best or best_size < 1024 * 1024:
        return best, best_size, False, _mime_type(best or "")

    real_size, head_ready, mime = _check_video_ready(best, hash_str)
    return best, real_size, head_ready, mime


def format_size(b: int) -> str:
    """将字节数格式化为人类可读的字符串。"""
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = math.floor(math.log(b) / math.log(1024))
    return f"{b / math.pow(1024, i):.1f} {units[i]}"


# ── BitTorrent engine ───────────────────────────────────
class TorrentEngine:
    """BitTorrent 下载引擎，管理缓存、优先级和播放状态。"""

    def __init__(self, cache_dir: str, max_size_gb: int) -> None:
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        self.session = lt.session()

        settings = self.session.get_settings()
        settings["alert_mask"] = int(lt.alert.category_t.status_notification)
        settings["connections_limit"] = 200
        settings["download_rate_limit"] = 0
        settings["upload_rate_limit"] = 0
        settings["checking_mem_usage"] = 1024  # 1GB RAM for faster hash checking
        self.session.apply_settings(settings)

        # hash -> { handle, magnet, added_at, last_access, video_idx, video_path, video_size }
        self.torrents: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

        self._stop = False
        self._alert_thread = threading.Thread(target=self._process_alerts, daemon=True)
        self._alert_thread.start()
        self._clean_thread = threading.Thread(target=self._periodic_clean, daemon=True)
        self._clean_thread.start()
        # Startup cleanup: evict orphaned dirs + enforce limit
        self._cleanup_orphaned()
        self._enforce_cache_limit()

        # Startup: preload all cached .torrent files so playback starts instantly.
        # Without this, every click-play has to re-add the torrent and wait for
        # libtorrent to initialize / check files.
        self._preload_thread = threading.Thread(target=self._preload_cached_torrents, daemon=True)
        self._preload_thread.start()

    def _preload_cached_torrents(self) -> None:
        """扫描 cache 目录，自动加载所有已缓存的 .torrent 文件。"""
        if not os.path.isdir(self.cache_dir):
            return
        loaded = 0
        for entry in os.scandir(self.cache_dir):
            if not entry.is_dir():
                continue
            hash_str = entry.name
            if len(hash_str) != 40:
                continue
            torrent_path = os.path.join(entry.path, f"{hash_str}.torrent")
            if not os.path.exists(torrent_path):
                continue
            # Skip if already loaded (shouldn't happen at startup, but be safe)
            with self.lock:
                if hash_str in self.torrents:
                    continue
            try:
                magnet = f"magnet:?xt=urn:btih:{hash_str}"
                self.add_torrent(magnet, prefetch=False)
                loaded += 1
            except Exception as e:
                log.warning(f"preload failed: {hash_str[:12]}... {e}")
        if loaded:
            log.info(f"preloaded {loaded} cached torrents from {self.cache_dir}")

    def _pick_video_file(self, ti: lt.torrent_info) -> tuple[int, int, str]:
        """从 torrent 文件中挑选 hhd800.com 主视频文件。"""
        fs = ti.files()
        hhd800_candidates = []
        for idx in range(fs.num_files()):
            name = fs.file_path(idx)
            size = fs.file_size(idx)
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            if any(p.search(name) for p in SPAM_PATTERNS):
                continue
            if "hhd800" in name.lower():
                hhd800_candidates.append((size, idx, name))
        if hhd800_candidates:
            hhd800_candidates.sort(reverse=True)
            return hhd800_candidates[0]
        # 没有 hhd800 视频文件，返回空标记
        return (0, -1, "")

    def _calc_prefetch_pieces(self, ti: lt.torrent_info, video_idx: int) -> tuple[int, int]:
        """计算预下载的 piece 范围（前 2%）。"""
        fs = ti.files()
        file_offset = fs.file_offset(video_idx)
        file_size = fs.file_size(video_idx)
        piece_length = ti.piece_length()
        prefetch_bytes = int(file_size * PREFETCH_PERCENT)
        start_piece = file_offset // piece_length
        end_piece = (file_offset + prefetch_bytes) // piece_length
        return start_piece, end_piece

    def _enforce_cache_limit(self) -> None:
        """LRU 缓存淘汰：当使用量超过 80% 限制时删除最旧的 torrent。"""
        total = self._get_cache_size()
        threshold = int(self.max_size_bytes * 0.8)
        if total <= threshold:
            return

        log.warning(f"cache eviction triggered: {format_size(total)} / {format_size(self.max_size_bytes)}")

        with self.lock:
            # Sort by last_access ascending (oldest first)
            candidates = sorted(
                self.torrents.items(),
                key=lambda x: x[1]["last_access"]
            )

        freed = 0
        for hash_str, info in candidates:
            if total - freed <= threshold:
                break
            # Protect torrents playing within 5 minutes
            if time.time() - info["last_access"] < 300:
                continue
            log.info(f"evicting torrent {hash_str[:12]}... (last_access {int(time.time() - info['last_access'])}s ago)")
            self.remove_torrent(hash_str)
            # remove_torrent deleted files, recalculate
            freed = total - self._get_cache_size()

        log.info(f"cache eviction done: freed {format_size(freed)}, current {format_size(self._get_cache_size())}")

    def add_torrent(self, magnet: str, prefetch: bool = False) -> dict[str, Any] | None:
        """添加一个 magnet 链接到下载队列。"""
        hash_str = self._extract_hash(magnet)
        if not hash_str:
            return None

        with self.lock:
            existing = self.torrents.get(hash_str)
        if existing:
            existing["last_access"] = time.time()
            # Re-pick video file in case logic changed (outside lock to avoid deadlock)
            if existing["handle"].status().has_metadata:
                self._on_metadata(existing["handle"])
            return existing

        # Check cache limit before adding
        self._enforce_cache_limit()

        save_path = os.path.join(self.cache_dir, hash_str)
        os.makedirs(save_path, exist_ok=True)

        params = lt.parse_magnet_uri(magnet)
        params.save_path = save_path
        # Disable auto_managed: we control piece priorities strictly.
        # Otherwise libtorrent overrides our sliding-window strategy.
        params.flags &= ~lt.torrent_flags.auto_managed
        # Also disable seed_mode to prevent progress from jumping to 100%
        # when sparse files already exist on disk.
        params.flags &= ~lt.torrent_flags.seed_mode
        # Magnet URI defaults to paused; resume so it actually connects to
        # trackers / DHT and downloads metadata.
        params.flags &= ~lt.torrent_flags.paused

        # Load cached metadata if available (skips peer discovery + metadata download)
        torrent_path = os.path.join(save_path, f"{hash_str}.torrent")
        if os.path.exists(torrent_path):
            try:
                ti = lt.torrent_info(torrent_path)
                params.ti = ti
                log.info(f"metadata cache hit: {hash_str[:12]}... ({ti.name()})")
            except Exception as e:
                log.warning(f"metadata cache load failed: {hash_str[:12]}... {e}")

        handle = self.session.add_torrent(params)

        info = {
            "handle": handle,
            "magnet": magnet,
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": None,
            "video_path": None,
            "video_size": 0,
            "ready": False,
            "prefetch": prefetch,
            "work_code": _extract_work_code(magnet) or None,
        }
        with self.lock:
            self.torrents[hash_str] = info

        # If metadata was loaded from local .torrent, run _on_metadata immediately
        # so video_idx / ready are populated before the first status query.
        if params.ti is not None:
            self._on_metadata(handle)

        return info

    def _extract_hash(self, magnet: str) -> str | None:
        """从 magnet 链接中提取 info hash。"""
        m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
        return m.group(1).lower() if m else None

    def _process_alerts(self) -> None:
        """后台线程：处理 libtorrent 的 alert 队列。"""
        while not self._stop:
            for alert in self.session.pop_alerts():
                self._handle_alert(alert)
            time.sleep(0.5)

    def _handle_alert(self, alert: lt.alert) -> None:
        """处理单个 libtorrent alert。"""
        if isinstance(alert, lt.metadata_received_alert):
            self._on_metadata(alert.handle)
        elif isinstance(alert, lt.torrent_checked_alert):
            # After file verification, re-sync tracker and reapply play priority
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    info = self.torrents[hash_str]
                    # Re-bootstrap tracker after checking — libtorrent zeros pieces
                    # during checking, which SEEK_HOLE falsely sees as data. The
                    # reset in _bootstrap_from_filesystem clears stale VERIFIED.
                    if info.get("tracker"):
                        info["tracker"]._bootstrap_from_filesystem()
                        info["tracker"]._overlay_have_piece(strict=True)
                    if not info.get("prefetch"):
                        # Always reapply: recheck may have invalidated pieces that
                        # were previously thought complete, and they need urgent
                        # priority again.
                        self._apply_play_priority(h, info)
        elif isinstance(alert, lt.torrent_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    self.torrents[hash_str]["ready"] = True
        elif isinstance(alert, lt.piece_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    tracker = self.torrents[hash_str].get("tracker")
                    if tracker:
                        tracker.on_piece_finished(alert.piece_index)
        elif isinstance(alert, lt.hash_failed_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    tracker = self.torrents[hash_str].get("tracker")
                    if tracker:
                        tracker.on_hash_failed(alert.piece_index)

    def _on_metadata(self, handle: lt.torrent_handle) -> None:
        """当 torrent metadata 下载完成后，选定视频文件并设置优先级。"""
        hash_str = str(handle.info_hash())
        with self.lock:
            if hash_str not in self.torrents:
                return
            info = self.torrents[hash_str]

        ti = handle.torrent_file()
        fs = ti.files()

        size, idx, name = self._pick_video_file(ti)
        if idx == -1:
            log.warning(f"metadata: {hash_str[:12]}... no hhd800 video file found")
            info["ready"] = False
            return

        info["video_idx"] = idx
        info["video_path"] = os.path.join(info["handle"].status().save_path, name)
        info["video_size"] = size
        code_from_file = _extract_work_code(name)
        if code_from_file:
            info["work_code"] = code_from_file

        # Create PieceStateTracker once — repeated calls from add_torrent polling
        # must NOT recreate it, or all verified-piece state is lost.
        if not info.get("tracker"):
            info["tracker"] = PieceStateTracker(
                handle=handle,
                video_idx=idx,
                video_size=size,
                path=info["video_path"],
            )

        file_prios = [0] * fs.num_files()
        file_prios[idx] = 4
        handle.prioritize_files(file_prios)
        log.debug(
            "_on_metadata: file priority set",
            extra={
                "hash": hash_str[:12],
                "video_idx": idx,
                "video_name": name,
                "video_size": size,
                "num_files": fs.num_files(),
            },
        )

        # 持久化 metadata，下次播放时无需重新寻找 peers 下载 metadata
        try:
            torrent_path = os.path.join(info["handle"].status().save_path, f"{hash_str}.torrent")
            info_sec = ti.info_section()
            entry = {"info": lt.bdecode(info_sec)}
            with open(torrent_path, "wb") as f:
                f.write(lt.bencode(entry))
            log.info(f"metadata saved: {hash_str[:12]}... ({ti.name()})")
        except Exception as e:
            log.warning(f"metadata save failed: {hash_str[:12]}... {e}")

        num_pieces = ti.num_pieces()
        if info.get("prefetch"):
            # Prefetch mode: first 2% pieces priority 4, rest 0
            start, end = self._calc_prefetch_pieces(ti, idx)
            piece_prios = [0] * num_pieces
            for p in range(start, min(end + 1, num_pieces)):
                piece_prios[p] = 4
            handle.prioritize_pieces(piece_prios)
            log.info(f"prefetch: {name} ({format_size(size)}) pieces {start}-{end}")
        else:
            # Default: all pieces priority 0 (strict on-demand).
            # head+tail urgent applied later by _apply_play_priority.
            # 只在首次 metadata 就绪时执行，避免重复 add_torrent 重置窗口
            if not info.get("_play_priority_applied"):
                piece_prios = [0] * num_pieces
                handle.prioritize_pieces(piece_prios)
                self._apply_play_priority(handle, info)
                info["_play_priority_applied"] = True
            log.info(f"added: {name} ({format_size(size)})")

        # If torrent resumed from cache and is in finished state, its have_piece
        # bitmap may be stale (doesn't match actual sparse file content).
        # Force a recheck to sync have_pieces with disk, otherwise head_ready
        # will stay false forever because libtorrent won't re-download pieces
        # it thinks are already complete.
        # Only do this ONCE — _on_metadata may be called again by add_torrent
        # when the torrent already exists, and repeated rechecks break playback.
        # NOTE: use a separate flag from "tracker"; tracker is created above
        # and would make this condition always false.
        if not info.get("_recheck_done"):
            status = handle.status()
            if status.state == lt.torrent_status.finished:
                handle.force_recheck()
                info["_recheck_done"] = True
                log.info(
                    f"recheck triggered: {hash_str[:12]}... (finished state, stale have_pieces)"
                )

        info["ready"] = True

    def _set_stream_window(
        self,
        h: lt.torrent_handle,
        info: dict[str, Any],
        time_sec: float,
        duration_sec: float,
        window_pcs: int = 30,
    ) -> bool:
        """严格按需：只下载 head + tail + 播放窗口，其余 piece 设为 0。"""
        if not h.status().has_metadata:
            return False
        ti = h.torrent_file()
        fs = ti.files()
        idx = info["video_idx"]
        if idx is None:
            return False

        num_pieces = ti.num_pieces()
        piece_length = ti.piece_length()
        file_offset = fs.file_offset(idx)
        file_size = fs.file_size(idx)
        start_piece = file_offset // piece_length
        end_piece = (file_offset + file_size) // piece_length

        # 窗口外设为 0（严格按需），窗口内 7
        piece_prios = [0] * num_pieces

        # Head urgent (moov-in-head + first frame)
        head_count = min(30, end_piece - start_piece + 1)
        for p in range(start_piece, min(start_piece + head_count, end_piece + 1)):
            piece_prios[p] = 7
            h.set_piece_deadline(p, 0)

        # Tail urgent (moov-in-tail)
        tail_count = min(30, end_piece - start_piece + 1)
        for p in range(max(start_piece, end_piece - tail_count + 1), end_piece + 1):
            piece_prios[p] = 7

        # 播放窗口
        ratio = min(1.0, max(0.0, time_sec / duration_sec)) if duration_sec > 0 else 0.0
        target_byte = int(file_size * ratio)
        target_piece = start_piece + (target_byte // piece_length)

        win_start = max(start_piece, target_piece - window_pcs)
        win_end = min(end_piece, target_piece + window_pcs)
        for p in range(win_start, win_end + 1):
            if piece_prios[p] == 0:
                piece_prios[p] = 7
                h.set_piece_deadline(p, 0)

        h.prioritize_pieces(piece_prios)
        h.set_sequential_download(False)
        log.debug(
            "_set_stream_window: pieces prioritized",
            extra={
                "hash": info['hash'][:12],
                "num_pieces": num_pieces,
                "start_piece": start_piece,
                "end_piece": end_piece,
                "head_count": head_count,
                "tail_count": tail_count,
                "window_pcs": window_pcs,
                "win_start": win_start if window_pcs > 0 else None,
                "win_end": win_end if window_pcs > 0 else None,
            },
        )
        return True

    def _apply_play_priority(self, h: lt.torrent_handle, info: dict[str, Any]) -> bool:
        """开始播放：只下载 head + tail，等待前端报告进度后滑动窗口。"""
        tracker = info.get("tracker")
        if tracker:
            count = tracker.request_head_tail(head_count=30, tail_count=30)
            log.info(
                f"play priority: {info['hash'][:12]}... head+tail via tracker",
                extra={"requested_pieces": count},
            )
            return True
        # Fallback to old _set_stream_window if tracker not yet created
        result = self._set_stream_window(h, info, 0.0, 0.0, window_pcs=0)
        if result:
            log.info(f"play priority: {info['hash'][:12]}... head+tail only, waiting for progress")
        return result

    def set_full_priority(self, hash_str: str) -> bool:
        """Play mode: head urgent, rest paused — ensure head ready first"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        h = info["handle"]
        result = self._apply_play_priority(h, info)
        if result:
            info["last_access"] = time.time()
        return result

    def apply_seek_priority(self, hash_str: str, time_sec: float, duration_sec: float) -> bool:
        """Seek：缩小窗口到 ±15 piece，快速定位目标位置。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        h = info["handle"]
        result = self._set_stream_window(h, info, time_sec, duration_sec, window_pcs=15)
        if result:
            log.info(
                f"seek priority: {hash_str[:12]}... t={time_sec:.1f}s "
                f"window=±15pcs strict"
            )
            info["last_access"] = time.time()
        return result

    def update_play_progress(self, hash_str: str, time_sec: float, duration_sec: float) -> bool:
        """正常播放中滑动窗口：±30 piece（约 2–4 分钟缓冲），其余不下载。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        h = info["handle"]
        result = self._set_stream_window(h, info, time_sec, duration_sec, window_pcs=30)
        if result:
            info["last_access"] = time.time()
        return result

    def get_status(self, hash_str: str) -> dict[str, Any] | None:
        """获取指定 torrent 的播放和下载状态。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return None

        h = info["handle"]
        s = h.status()

        # Fast path: if video_path is known, skip the directory scan in
        # find_video_state. The frontend polls this once per second; avoiding
        # os.walk saves syscalls under concurrent load.
        local_path = info.get("video_path")
        if local_path and os.path.exists(local_path):
            local_size, head_ready_fs, mime = _check_video_ready(local_path, hash_str)
        else:
            local_path, local_size, head_ready_fs, mime = find_video_state(hash_str)

        # Use tracker head_ready if available (O(pieces) instead of filesystem scan)
        tracker = info.get("tracker")
        if tracker and local_path:
            moov_start, moov_end = _scan_mp4_moov(local_path)
            if moov_end > 0:
                head_ready = tracker.head_ready(moov_start, moov_end)
            else:
                head_ready = head_ready_fs
        else:
            head_ready = head_ready_fs

        # libtorrent finished/seeding 状态下 progress 恒为 100%，用实际磁盘大小修正
        progress = s.progress * 100
        if s.state in (lt.torrent_status.finished, lt.torrent_status.seeding):
            if info.get("video_size", 0) > 0:
                progress = (local_size / info["video_size"]) * 100

        return {
            "hash": hash_str,
            "name": s.name,
            "work_code": info.get("work_code") or _extract_work_code(s.name) or "",
            "ready": info["ready"] and s.has_metadata,
            "cached": local_size > 1024 * 1024,
            "head_ready": head_ready,
            "peers": s.num_peers,
            "progress": progress,
            "download_rate": s.download_rate,
            "upload_rate": s.upload_rate,
            "video_file": os.path.basename(info["video_path"]) if info["video_path"] else None,
            "video_size": info["video_size"],
            "local_size": local_size,
            "mime": mime,
            "state": str(s.state),
            "verified_pieces": tracker.verified_count() if tracker else 0,
        }

    def get_all_status(self) -> list[dict[str, Any]]:
        """获取所有 torrent 的状态列表。"""
        with self.lock:
            hashes = list(self.torrents.keys())
        return [self.get_status(h) for h in hashes]

    def remove_torrent(self, hash_str: str) -> bool:
        """移除指定 torrent 并删除其缓存文件。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False

        self.session.remove_torrent(info["handle"])
        save_path = os.path.join(self.cache_dir, hash_str)
        try:
            shutil.rmtree(save_path, ignore_errors=True)
        except Exception as e:
            log.error(f"remove error: {e}")

        with self.lock:
            if hash_str in self.torrents:
                del self.torrents[hash_str]
        return True

    def _get_cache_size(self) -> int:
        """计算当前缓存目录占用的实际磁盘大小（字节）。"""
        total = 0
        if not os.path.exists(self.cache_dir):
            return 0
        for dirpath, dirnames, filenames in os.walk(self.cache_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    st = os.stat(fp)
                    total += st.st_blocks * 512
                except OSError:
                    pass
        return total

    def _periodic_clean(self) -> None:
        """后台线程：定期检查并清理超出限制的缓存。"""
        while not self._stop:
            time.sleep(CACHE_CLEAN_INTERVAL_SEC)
            if self._stop:
                break
            try:
                self._enforce_cache_limit()
            except Exception as e:
                log.error(f"periodic clean error: {e}")

    def _cleanup_orphaned(self) -> None:
        """清理不在引擎管理列表中的孤儿缓存目录。

        安全保护：如果 self.torrents 为空（刚启动尚未添加任何 torrent），
        跳过清理，避免误删用户已有的缓存数据。
        """
        if not os.path.exists(self.cache_dir):
            return
        with self.lock:
            known = set(self.torrents.keys())
        if not known:
            # Engine 刚启动，torrents 字典为空，不要误删缓存
            return
        freed = 0
        for name in os.listdir(self.cache_dir):
            path = os.path.join(self.cache_dir, name)
            if not os.path.isdir(path):
                continue
            if name in known:
                continue
            try:
                size = sum(
                    os.stat(os.path.join(dp, f)).st_blocks * 512
                    for dp, _, files in os.walk(path)
                    for f in files
                )
                shutil.rmtree(path, ignore_errors=True)
                freed += size
                log.info(f"cleaned orphaned cache: {name} ({format_size(size)})")
            except Exception as e:
                log.warning(f"cleanup orphaned {name} failed: {e}")
        if freed:
            log.info(f"orphaned cleanup done: freed {format_size(freed)}")

    def shutdown(self) -> None:
        """关闭引擎，停止 alert 处理线程。"""
        self._stop = True
        self._alert_thread.join(timeout=5)
        self._clean_thread.join(timeout=5)
