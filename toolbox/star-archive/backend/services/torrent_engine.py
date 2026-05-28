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
    """Check if [start, end] range has data on disk (SEEK_HOLE after fsync).

    fsync() flushes page cache so SEEK_HOLE sees real disk extents.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
            hole = os.lseek(fd, start, os.SEEK_HOLE)
            return hole >= end + 1
        finally:
            os.close(fd)
    except Exception:
        return False


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
        settings["alert_mask"] = int(
            lt.alert.category_t.status_notification | lt.alert.category_t.progress_notification
        )
        settings["connections_limit"] = 200
        settings["download_rate_limit"] = 0
        settings["upload_rate_limit"] = 0
        settings["checking_mem_usage"] = 1024  # 1GB RAM for faster hash checking
        settings["alert_queue_size"] = 10000  # prevent alert drop under load
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

    def _pick_video_file(self, ti: lt.torrent_info) -> tuple[int, int, str, bool]:
        """从 torrent 文件中挑选视频文件。优先 hhd800.com 高清源，否则选最大的。"""
        fs = ti.files()
        hhd800_candidates = []
        all_video_candidates = []
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
            all_video_candidates.append((size, idx, name))
        if hhd800_candidates:
            hhd800_candidates.sort(reverse=True)
            return (*hhd800_candidates[0], True)
        # 没有 hhd800 时回退到最大的视频文件
        if all_video_candidates:
            all_video_candidates.sort(reverse=True)
            return (*all_video_candidates[0], False)
        return (0, -1, "", False)

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

    # ── Tiered cache scoring ────────────────────────────────

    def _get_tier(self, info: dict[str, Any]) -> str:
        """Classify torrent into L1/L2/L3/L4 based on value.

        L1 (hot):     played within 24h  → never evict
        L2 (warm):    100% complete + accessed within 7d
        L3 (seed):    100% complete + cold (>7d)
        L4 (fragment): incomplete + cold  → punch hole or evict
        """
        now = time.time()
        last_play = info.get("_last_play_time", 0)
        last_access = info["last_access"]
        progress = info.get("progress", 0)

        if last_play and now - last_play < 86400:
            return "hot"
        if progress >= 99.9:
            if last_access and now - last_access < 604800:
                return "warm"
            return "seed"
        return "fragment"

    def _cache_score(self, info: dict[str, Any]) -> float:
        """Higher score = more valuable, less evictable.

        Combines: play history, completion, recency, value-per-GB.
        """
        now = time.time()
        last_play = info.get("_last_play_time", 0)
        last_access = info["last_access"]
        progress = info.get("progress", 0)
        size = info.get("video_size", 1024)
        play_count = info.get("_play_count", 0)

        hours_since_play = (now - last_play) / 3600 if last_play else 9999
        heat = math.exp(-hours_since_play / 168)  # 7-day half-life

        # Play bonus: played torrents are an order of magnitude more valuable
        play_bonus = 1000.0 * play_count

        # Completion: 100% = 1000 pts, 50% = 500 pts
        completion_score = progress * 10

        # Value density: completed 6GB > incomplete 6GB
        size_gb = size / (1024 ** 3)
        value_per_gb = (play_bonus + completion_score) / max(size_gb, 0.1)

        return value_per_gb * heat + play_bonus

    def _punch_hole_middle_pieces(self, hash_str: str) -> int:
        """L4降级: punch holes in non-head-tail pieces to free disk space.

        Returns bytes freed. Only operates on completed (L3) torrents.
        """
        info = self.torrents.get(hash_str)
        if not info:
            return 0
        tracker = info.get("tracker")
        path = info.get("video_path")
        if not tracker or not path or not os.path.exists(path):
            return 0

        piece_length = tracker.piece_length
        file_offset = tracker.file_offset
        freed = 0

        # Protect head+tail (30 pieces each side)
        head_end = tracker.start_piece + 30
        tail_start = tracker.end_piece - 30

        fd = os.open(path, os.O_WRONLY)
        try:
            for p in range(tracker.start_piece, tracker.end_piece + 1):
                if p < head_end or p > tail_start:
                    continue
                if not tracker.is_verified(p):
                    continue
                start = p * piece_length - file_offset
                length = piece_length
                try:
                    os.fallocate(fd, os.FALLOC_FL_PUNCH_HOLE | os.FALLOC_FL_KEEP_SIZE, start, length)
                    freed += length
                except OSError:
                    pass
        finally:
            os.close(fd)

        if freed > 0:
            tracker._bootstrap_from_filesystem()
            log.info(
                f"punch hole: {hash_str[:12]}... freed {format_size(freed)} "
                f"(kept head+tail {format_size(30 * piece_length * 2)})"
            )
        return freed

    def _enforce_cache_limit(self) -> None:
        """Tiered cache eviction: progressive, score-based, with L1 protection.

        Soft limit: 95% of max. When exceeded, evict ONE lowest-score torrent
        per cycle. L1 (hot) torrents are protected at soft limit.

        Hard limit: 120% of max. When exceeded, force-evict even L1 (hot)
        torrents to prevent disk exhaustion. Hard limit overrides tier protection.
        L3 torrents are downgraded to L4 (punch hole) before full eviction.
        """
        total = self._get_cache_size()
        soft_threshold = int(self.max_size_bytes * 0.95)
        hard_threshold = int(self.max_size_bytes * 1.20)

        if total <= soft_threshold:
            return

        log.warning(
            f"cache eviction triggered: {format_size(total)} / {format_size(self.max_size_bytes)}"
        )

        force_evict_hot = total > hard_threshold
        if force_evict_hot:
            log.error(
                f"cache HARD LIMIT exceeded: {format_size(total)} / {format_size(hard_threshold)}. "
                f"Force-evicting including hot torrents."
            )

        with self.lock:
            candidates = [
                (h, i) for h, i in self.torrents.items()
                if force_evict_hot or self._get_tier(i) != "hot"
            ]

        if not candidates:
            log.error("cache eviction: no candidates available even under hard limit")
            return

        # Sort by score ascending (least valuable first)
        candidates.sort(key=lambda x: self._cache_score(x[1]))
        hash_str, info = candidates[0]

        tier = self._get_tier(info)

        # L3 (completed, cold) → punch hole before full eviction
        if tier == "seed" and info.get("progress", 0) >= 99.9:
            freed = self._punch_hole_middle_pieces(hash_str)
            if freed > 0:
                new_size = self._get_cache_size()
                log.info(
                    f"eviction: downgraded {hash_str[:12]}... L3→L4, "
                    f"freed {format_size(freed)}, current {format_size(new_size)}"
                )
                if new_size <= soft_threshold:
                    return

        # L2/L4 or punch-hole-insufficient L3 → full eviction
        log.info(
            f"evicting torrent {hash_str[:12]}... "
            f"(tier={tier}, score={self._cache_score(info):.0f}, "
            f"size={format_size(info.get('video_size', 0))})"
        )
        self.remove_torrent(hash_str)
        new_size = self._get_cache_size()
        log.info(
            f"cache eviction done: current {format_size(new_size)}"
        )

    def add_torrent(self, magnet: str, prefetch: bool = False) -> dict[str, Any] | None:
        """添加一个 magnet 链接到下载队列。"""
        hash_str = self._extract_hash(magnet)
        if not hash_str:
            return None

        with self.lock:
            existing = self.torrents.get(hash_str)
        if existing:
            existing["last_access"] = time.time()
            # Only re-run _on_metadata if tracker is missing (first time metadata
            # becomes available after a bare-hash add). Repeated _on_metadata
            # calls spam logs, overwrite .torrent files, and can race with
            # bootstrap/recheck state transitions.
            if existing["handle"].status().has_metadata and not existing.get("tracker"):
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

        # Load cached metadata if available (skips peer discovery + metadata download).
        # Skip cache if video file does not exist — using cached metadata when
        # file is missing causes libtorrent to finish immediately because sparse
        # file size matches, leading to finished-state deadlock.
        torrent_path = os.path.join(save_path, f"{hash_str}.torrent")
        video_exists = False
        if os.path.exists(save_path):
            for root, dirs, files in os.walk(save_path):
                for f in files:
                    if f.lower().endswith((".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv")):
                        video_exists = True
                        break
                if video_exists:
                    break
        if os.path.exists(torrent_path) and video_exists:
            try:
                ti = lt.torrent_info(torrent_path)
                if str(ti.info_hash()) != hash_str:
                    log.warning(
                        f"metadata cache mismatch: {hash_str[:12]}... "
                        f"file hash={str(ti.info_hash())[:12]}... deleting stale cache"
                    )
                    os.remove(torrent_path)
                else:
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
            "_last_play_time": 0,
            "_play_count": 0,
            "progress": 0.0,
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
            # After file verification, re-bootstrap from disk (ground truth).
            # Do NOT trust libtorrent's have_piece() — recheck may have read
            # stale page-cache zeros and falsely marked pieces complete.
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    info = self.torrents[hash_str]
                    if info.get("tracker"):
                        info["tracker"]._bootstrap_from_filesystem()
                    if not info.get("prefetch"):
                        self._apply_play_priority(h, info)
        elif isinstance(alert, lt.torrent_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                info = self.torrents.get(hash_str)
            if not info:
                return
            tracker = info.get("tracker")
            # finished state does NOT mean data is actually on disk.
            # Re-scan with SEEK_HOLE (ground truth) before trusting it.
            if tracker:
                tracker._bootstrap_from_filesystem()
            if tracker and tracker.head_ready():
                with self.lock:
                    if hash_str in self.torrents:
                        self.torrents[hash_str]["ready"] = True
            else:
                log.warning(
                    f"finished but head not ready: {hash_str[:12]}... "
                    f"disk scan shows holes, will readd on next stream request"
                )
        elif isinstance(alert, lt.piece_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    tracker = self.torrents[hash_str].get("tracker")
                    if tracker:
                        tracker.on_piece_finished(alert.piece_index)
                        if tracker.start_piece <= alert.piece_index <= tracker.end_piece:
                            log.info(
                                f"piece finished: {hash_str[:12]}... piece={alert.piece_index} "
                                f"verified={tracker.verified_count()}/{tracker.end_piece - tracker.start_piece + 1} "
                                f"head_ready={tracker.head_ready()}"
                            )
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

        size, idx, name, is_hd = self._pick_video_file(ti)
        if idx == -1:
            log.warning(f"metadata: {hash_str[:12]}... no video file found")
            info["ready"] = False
            return

        if not is_hd:
            log.info(f"metadata: {hash_str[:12]}... no hhd800, using fallback {name}")
        info["quality"] = "HD" if is_hd else "SD"
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

        # Scan moov once and cache into info + tracker. Eliminates repeated
        # _scan_mp4_moov calls on every /api/check/ poll (was 32MB disk read/s).
        #
        # CRITICAL: if the file is empty/too small, _scan_mp4_moov returns (0,0).
        # Do NOT guess tail-moov here — if the guess is wrong (moov is actually
        # in head), _set_stream_window will only download tail and moov will
        # never arrive. Leave moov unknown so _set_stream_window probes both
        # head and tail. get_status() will retry scan as data arrives.
        video_path = info.get("video_path")
        if video_path and os.path.exists(video_path):
            # Force re-scan if moov_end is missing or was previously zero.
            # Stale zero from a prior add can linger in existing info dicts.
            need_scan = "moov_end" not in info or info.get("moov_end", 0) == 0
            if need_scan:
                moov_start, moov_end = _scan_mp4_moov(video_path)
                if moov_end > 0:
                    info["moov_start"] = moov_start
                    info["moov_end"] = moov_end
                    if info.get("tracker"):
                        info["tracker"].set_moov_range(moov_start, moov_end)
                        log.info(
                            f"moov scanned: {hash_str[:12]}... "
                            f"range={moov_start}-{moov_end}"
                        )
                else:
                    log.info(
                        f"moov not found yet: {hash_str[:12]}... "
                        f"will probe tail until data arrives"
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
            # Strict on-demand: never auto-start download. Only /torrent/resume
            # or /stream/ (read_video_range -> seek_priority) triggers piece download.
            if not info.get("_play_priority_applied"):
                piece_prios = [0] * num_pieces
                handle.prioritize_pieces(piece_prios)
                info["_play_priority_applied"] = True
                log.info(f"metadata: {hash_str[:12]}... all pieces paused, waiting for playback")
            else:
                log.info(f"added: {name} ({format_size(size)})")

        # ── Architecture: bootstrap-first verification ─────────────────
        # For finished torrents, use SEEK_HOLE bootstrap (seconds) to verify
        # disk state. NEVER call force_recheck() — recheck reads page cache
        # and produces false positives, leading to finished-state deadlock
        # where libtorrent refuses to re-download holes.
        if not info.get("_recheck_done"):
            status = handle.status()
            if status.state == lt.torrent_status.finished:
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
                            f"disk scan shows missing data, will re-download"
                        )
                # Do NOT force_recheck — it causes finished-state deadlock.
                # Let _set_stream_window set urgent priorities instead.

        info["ready"] = True

    def _set_stream_window(
        self,
        h: lt.torrent_handle,
        info: dict[str, Any],
        time_sec: float,
        duration_sec: float,
        window_pcs: int = 30,
    ) -> bool:
        """滑动窗口策略：窗口内 urgent(7)，已下载保留(1)，其余停止(0)。

        不再全量重置为 0 —— 那样会丢弃已下载的 piece，导致播放时反复重新下载，
        体验极差。改为保留已下载 piece（priority=1），只把未下载且不在窗口内的设 0。
        """
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

        tracker = info.get("tracker")
        head_ready = tracker.head_ready() if tracker else False
        moov_end = info.get("moov_end", 0)

        # 读取当前优先级，做增量修改（避免全量重置丢弃已下载数据）
        piece_prios = list(h.piece_priorities())
        changed = False

        # ---- moov / head_ready 处理 ----
        moov_pieces: set[int] = set()
        if not head_ready and moov_end > 0 and tracker:
            moov_start_piece = (tracker.file_offset + info.get("moov_start", 0)) // piece_length
            moov_end_piece = (tracker.file_offset + moov_end) // piece_length
            for p in range(max(start_piece, moov_start_piece), min(end_piece, moov_end_piece) + 1):
                if tracker and not tracker.is_verified(p):
                    moov_pieces.add(p)
        elif not head_ready:
            # moov unknown: try scan now — file may have grown since _on_metadata
            if tracker and info.get("video_path") and os.path.exists(info["video_path"]):
                scanned_start, scanned_end = _scan_mp4_moov(info["video_path"])
                if scanned_end > 0:
                    info["moov_start"] = scanned_start
                    info["moov_end"] = scanned_end
                    tracker.set_moov_range(scanned_start, scanned_end)
                    moov_end = scanned_end
                    log.info(
                        f"moov found on-the-fly: {info['hash'][:12]}... "
                        f"range={scanned_start}-{scanned_end}"
                    )
            if moov_end > 0 and tracker:
                moov_start_piece = (tracker.file_offset + info.get("moov_start", 0)) // piece_length
                moov_end_piece = (tracker.file_offset + moov_end) // piece_length
                for p in range(max(start_piece, moov_start_piece), min(end_piece, moov_end_piece) + 1):
                    if not tracker.is_verified(p):
                        moov_pieces.add(p)
            else:
                # Still unknown: probe tail only (tail-moov is far more common)
                probe_count = min(20, end_piece - start_piece + 1)
                for p in range(max(start_piece, end_piece - probe_count + 1), end_piece + 1):
                    moov_pieces.add(p)

        # ---- 播放窗口 ----
        ratio = min(1.0, max(0.0, time_sec / duration_sec)) if duration_sec > 0 else 0.0
        target_byte = int(file_size * ratio)
        target_piece = start_piece + (target_byte // piece_length)

        win_start = max(start_piece, target_piece - window_pcs)
        win_end = min(end_piece, target_piece + window_pcs)

        urgent_count = 0
        retain_count = 0
        zero_count = 0

        for p in range(start_piece, end_piece + 1):
            in_window = win_start <= p <= win_end
            in_moov = p in moov_pieces
            is_verified = tracker.is_verified(p) if tracker else False

            if in_window or in_moov:
                # 窗口内 / moov 内：urgent
                if piece_prios[p] != 7:
                    piece_prios[p] = 7
                    h.set_piece_deadline(p, 0)
                    changed = True
                urgent_count += 1
            elif is_verified:
                # 已下载：保留（priority=1），不丢弃，libtorrent 空闲时还可做种
                if piece_prios[p] != 1:
                    piece_prios[p] = 1
                    changed = True
                retain_count += 1
            else:
                # 未下载且不在窗口：停止
                if piece_prios[p] != 0:
                    piece_prios[p] = 0
                    changed = True
                zero_count += 1

        if changed:
            h.prioritize_pieces(piece_prios)
        h.set_sequential_download(False)
        log.debug(
            "_set_stream_window: sliding window",
            extra={
                "hash": info['hash'][:12],
                "head_ready": head_ready,
                "moov_known": moov_end > 0,
                "window_pcs": window_pcs,
                "win_start": win_start,
                "win_end": win_end,
                "urgent": urgent_count,
                "retain": retain_count,
                "zero": zero_count,
            },
        )
        return True

    def _apply_play_priority(self, h: lt.torrent_handle, info: dict[str, Any]) -> bool:
        """开始播放：全量重置后只下载必要片段（moov + 极小窗口），等待前端报告进度。

        不再使用 tracker.request_head_tail() —— 那个方法不会重置其他 piece 的优先级，
        导致 libtorrent 默认优先级下的 piece 继续下载，形成"遍地开花"。
        """
        result = self._set_stream_window(h, info, 0.0, 0.0, window_pcs=0)
        if result:
            log.info(f"play priority: {info['hash'][:12]}... strict head+moov only")
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
                f"window=±15pcs"
            )
            info["last_access"] = time.time()
        return result

    def update_play_progress(self, hash_str: str, time_sec: float, duration_sec: float) -> bool:
        """正常播放中滑动窗口：±30 piece（约 2–4 分钟缓冲），已下载保留。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        h = info["handle"]
        result = self._set_stream_window(h, info, time_sec, duration_sec, window_pcs=30)
        if result:
            info["last_access"] = time.time()
        return result

    def set_keep_cache(self, hash_str: str, keep: bool = True) -> None:
        """标记该 torrent 是否需要在暂停后保留缓存。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if info:
            info["keep_cache"] = keep

    def pause_download(self, hash_str: str) -> bool:
        """暂停下载：将所有 piece 优先级设为 0。
        非第一个作品（keep_cache=false）直接删除 torrent 和缓存文件。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        tracker = info.get("tracker")
        if tracker:
            tracker.reset_priorities()
        info["_paused"] = True

        keep_cache = info.get("keep_cache", False)
        if not keep_cache:
            log.info(f"pause download: {hash_str[:12]}... non-primary, removing torrent")
            self.remove_torrent(hash_str)
        else:
            log.info(f"pause download: {hash_str[:12]}... primary, keeping cache")
        return True

    def resume_download(self, hash_str: str, time_sec: float, duration_sec: float) -> bool:
        """恢复下载：重新设置 moov + 当前窗口。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        info["_paused"] = False
        h = info["handle"]
        result = self._set_stream_window(h, info, time_sec, duration_sec, window_pcs=30)
        if result:
            log.info(f"resume download: {hash_str[:12]}... t={time_sec:.1f}s")
            info["last_access"] = time.time()
        return result

    def get_status(self, hash_str: str) -> dict[str, Any] | None:
        """获取指定 torrent 的播放和下载状态。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return None

        # Keep alive: any status query from frontend counts as activity.
        info["last_access"] = time.time()

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

        # Use tracker head_ready if available (O(1) POPCNT, no disk read).
        # _on_metadata caches moov into info; if missing fall back to fs scan.
        tracker = info.get("tracker")
        if tracker and local_path:
            if tracker._moov_pc > 0:
                head_ready = tracker.head_ready()
            else:
                # Retry scan: data may have arrived since _on_metadata
                moov_start, moov_end = _scan_mp4_moov(local_path)
                if moov_end > 0:
                    info["moov_start"] = moov_start
                    info["moov_end"] = moov_end
                    tracker.set_moov_range(moov_start, moov_end)
                    log.info(
                        f"moov found on retry: {hash_str[:12]}... "
                        f"range={moov_start}-{moov_end}"
                    )
                else:
                    log.debug(
                        f"moov retry still missing: {hash_str[:12]}... "
                        f"will keep probing tail"
                    )
                head_ready = tracker.head_ready() if tracker._moov_pc > 0 else False
        else:
            head_ready = head_ready_fs

        # libtorrent finished/seeding 状态下 progress 恒为 100%，用实际磁盘大小修正
        progress = s.progress * 100
        if s.state in (lt.torrent_status.finished, lt.torrent_status.seeding):
            if info.get("video_size", 0) > 0:
                progress = (local_size / info["video_size"]) * 100

        # Persist progress for tiered cache scoring
        info["progress"] = progress

        # 校验期间不应该认为 ready，避免前端在 recheck 时开始播放后卡住
        checking_states = (lt.torrent_status.checking_files, lt.torrent_status.checking_resume_data)
        is_ready = info["ready"] and s.has_metadata and s.state not in checking_states

        return {
            "hash": hash_str,
            "name": s.name,
            "work_code": info.get("work_code") or _extract_work_code(s.name) or "",
            "ready": is_ready,
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
            "quality": info.get("quality", "SD"),
            "piece_segments": tracker.get_lane_segments(30) if tracker else [],
            "tier": self._get_tier(info),
        }

    def touch(self, hash_str: str) -> None:
        """Update last_access to prevent GC eviction.

        Called by high-frequency endpoints (/stream, /api/check) that do not
        go through get_status() but still indicate active user interest.
        Only updates last_access; _last_play_time is managed by stream_router
        to avoid promoting every checked torrent to L1 (hot) tier.
        """
        with self.lock:
            info = self.torrents.get(hash_str)
        if info:
            info["last_access"] = time.time()

    def get_all_status(self) -> list[dict[str, Any]]:
        """获取所有 torrent 的状态列表。"""
        with self.lock:
            hashes = list(self.torrents.keys())
        return [self.get_status(h) for h in hashes]

    def _readd_torrent(self, hash_str: str) -> dict[str, Any] | None:
        """Remove and re-add a torrent to clear libtorrent's stale finished state.

        Preserves files on disk. Restores moov range and play state.
        CRITICAL: fsync() before re-add so recheck reads actual disk state,
        not stale page-cache zeros that produce false positives.
        """
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return None

        magnet = info["magnet"]
        prefetch = info.get("prefetch", False)
        saved: dict[str, Any] = {
            "last_access": info.get("last_access", time.time()),
            "work_code": info.get("work_code"),
            "_last_play_time": info.get("_last_play_time", 0),
            "_play_count": info.get("_play_count", 0),
            "_recheck_done": info.get("_recheck_done", False),
        }
        moov_start = info.get("moov_start")
        moov_end = info.get("moov_end")
        if moov_start:
            saved["moov_start"] = moov_start
        if moov_end:
            saved["moov_end"] = moov_end
        path = info.get("video_path")

        with self.lock:
            info = self.torrents.pop(hash_str, None)
        if not info:
            return None

        try:
            # option=0: do NOT delete files
            self.session.remove_torrent(info["handle"], 0)
        except Exception as e:
            log.warning(f"_readd_torrent remove failed: {e}")

        # CRITICAL: delete ALL files including .torrent so libtorrent re-downloads
        # metadata from peers. Keeping .torrent causes libtorrent to finish
        # immediately on add (sparse file size matches), creating deadlock loop.
        save_path = os.path.join(self.cache_dir, hash_str)
        if os.path.exists(save_path):
            try:
                shutil.rmtree(save_path)
            except Exception as e:
                log.warning(f"_readd_torrent rmtree failed: {e}")

        # Give libtorrent time to release file descriptors
        time.sleep(0.3)

        # Rate-limit readd to prevent infinite loops when libtorrent
        # repeatedly false-positives into finished state.
        now = time.time()
        last_readd = info.get("_last_readd_time", 0)
        if now - last_readd < 60:
            log.warning(
                f"readd throttled: {hash_str[:12]}... "
                f"last={int(now - last_readd)}s ago"
            )
            return None
        saved["_last_readd_time"] = now

        new_info = self.add_torrent(magnet, prefetch=prefetch)
        if new_info:
            new_info.update(saved)
            tracker = new_info.get("tracker")
            moov_start = saved.get("moov_start")
            moov_end = saved.get("moov_end")
            if moov_start and moov_end and tracker:
                tracker.set_moov_range(moov_start, moov_end)
            log.info(f"readded torrent: {hash_str[:12]}... to clear finished deadlock")
        return new_info

    def remove_torrent(self, hash_str: str) -> bool:
        """移除指定 torrent 并删除其缓存文件。

        先从 torrents dict 中移除并调用 session.remove_torrent，让 libtorrent
        释放文件描述符，再等待一小段时间后删除目录，避免产生 (deleted) 孤儿文件
        占用磁盘空间。
        """
        with self.lock:
            info = self.torrents.pop(hash_str, None)
        if not info:
            return False

        try:
            self.session.remove_torrent(info["handle"])
        except Exception as e:
            log.warning(f"remove_torrent session.remove failed: {e}")

        # 给 libtorrent 后台线程释放文件描述符的时间窗口
        time.sleep(0.5)

        save_path = os.path.join(self.cache_dir, hash_str)
        for attempt in range(3):
            try:
                shutil.rmtree(save_path, ignore_errors=False)
                break
            except Exception as e:
                if attempt < 2:
                    log.warning(f"remove retry {attempt + 1} for {hash_str[:12]}: {e}")
                    time.sleep(0.5)
                else:
                    log.error(f"remove final error for {hash_str[:12]}: {e}")
                    # 最后一次尝试忽略错误，至少清理掉能删的文件
                    shutil.rmtree(save_path, ignore_errors=True)
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
        """关闭引擎，停止后台线程并释放 libtorrent session 资源。"""
        self._stop = True
        self._alert_thread.join(timeout=5)
        self._clean_thread.join(timeout=5)
        self._preload_thread.join(timeout=5)
        # Remove all torrents to release file handles (critical in tests)
        for hash_str in list(self.torrents.keys()):
            try:
                info = self.torrents.get(hash_str)
                if info and info.get("handle"):
                    self.session.remove_torrent(info["handle"])
            except Exception:
                pass
        self.session.pause()
