from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import threading
import time
from typing import Any

import duckdb
import libtorrent as lt

from core import get_logger
from core.events import publish_event
from .piece_tracker import PieceStateTracker

log = get_logger("torrent-engine")

# ── Config ──────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_SCRIPT_DIR, "cache", "torrent")

MAX_CACHE_SIZE_GB = 0   # 0 means auto-calculate as 60% of disk capacity
PREFETCH_COUNT = 13
PREFETCH_PERCENT = 0.02
CACHE_CLEAN_INTERVAL_SEC = 60  # Background cleanup interval
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".webm"}
SPAM_PATTERNS = [re.compile(p, re.I) for p in [
    r"game pack", r"996gg", r"^\d+\.txt$", r"^readme", r"\.url$", r"\.txt$"
]]

# Common work-code format matcher
_WORK_CODE_RE = re.compile(r"[A-Z]{2,6}-\d{3,5}", re.I)

# Cache MP4 moov scan results: path -> (moov_start, moov_end).
# Moov position never changes for a given file, so caching is safe.
# We only cache successful scans (moov_end > 0) to avoid caching
# tail-moov files that aren't fully downloaded yet.
_MOOV_CACHE: dict[str, tuple[int, int]] = {}


def _extract_work_code(name: str) -> str | None:
    """Extract work code from file name or torrent name (e.g., ABC-123)."""
    if not name:
        return None
    m = _WORK_CODE_RE.search(name)
    return m.group(0).upper() if m else None

os.makedirs(CACHE_DIR, exist_ok=True)


def _scan_mp4_moov(path: str, max_read: int = 16 * 1024 * 1024) -> tuple[int, int]:
    """Scan MP4 file for moov box start and end positions.

    Returns (moov_start, moov_end). moov_start=0 and moov_end>0 means head-moov.
    moov_start>0 means tail-moov. Returns (0, 0) if not found.
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
    """Return MIME type based on file extension."""
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


def find_video_state(hash_str: str, preferred_path: str | None = None) -> tuple[str | None, int, bool, str]:
    """Find video file and check if enough head data is downloaded for playback.

    If preferred_path (target file from _pick_video_file) is provided,
    use it first to avoid mistakenly selecting ad files that have downloaded more.

    Returns: (file_path, actual_disk_size, head_ready, mime_type)
    """
    # Prefer the target file already selected by _pick_video_file
    if preferred_path and os.path.exists(preferred_path):
        ext = os.path.splitext(preferred_path)[1].lower()
        if ext in VIDEO_EXTS:
            real_size, head_ready, mime = _check_video_ready(preferred_path, hash_str)
            return preferred_path, real_size, head_ready, mime

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
    """Format byte count as human-readable string."""
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = math.floor(math.log(b) / math.log(1024))
    return f"{b / math.pow(1024, i):.1f} {units[i]}"


# ── BitTorrent engine ───────────────────────────────────
def _get_disk_total_bytes(path: str) -> int:
    """Get total capacity (bytes) of the partition containing path."""
    try:
        st = os.statvfs(path)
        return st.f_blocks * st.f_frsize
    except Exception:
        return 0


class TorrentEngine:
    """BitTorrent download engine; manages cache, priorities, and playback state."""

    def __init__(self, cache_dir: str, max_size_gb: int) -> None:
        self.cache_dir = cache_dir
        if max_size_gb <= 0:
            disk_total = _get_disk_total_bytes(cache_dir)
            self.max_size_bytes = int(disk_total * 0.6)
            log.info(f"cache limit auto: {format_size(self.max_size_bytes)} (60% of {format_size(disk_total)})")
        else:
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
        # CRITICAL: disable mmap storage to avoid finished-state deadlock.
        # libtorrent 2.0 mmap creates full-size sparse files on add, then
        # force_recheck reads page-cache zeros and falsely marks pieces
        # complete. POSIX storage does not have this bug.
        settings["mmap_file_size_cutoff"] = 0
        self.session.apply_settings(settings)
        # Verify setting was applied
        applied = self.session.get_settings().get("mmap_file_size_cutoff")
        log.info(f"session mmap_file_size_cutoff={applied}")

        # hash -> { handle, magnet, added_at, last_access, video_idx, video_path, video_size }
        self.torrents: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

        # Status cache: avoid repeated disk I/O on get_status / get_all_status
        self._status_cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._status_cache_ttl = 2.0  # seconds
        self._status_cache_lock = threading.Lock()

        # user-liked hashes: protected from eviction
        self.liked_hashes: set[str] = set()

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

        # Capture main event loop for cross-thread SSE publishing
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None

    def _emit_event(self, event: str, data: dict[str, Any]) -> None:
        """Publish an SSE event from the alert thread (cross-thread safe)."""
        if self._main_loop:
            try:
                asyncio.run_coroutine_threadsafe(publish_event(event, data), self._main_loop)
            except Exception:
                pass

    def _preload_cached_torrents(self) -> None:
        """Scan cache directory and auto-load all cached .torrent files."""
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
        """Pick video file from torrent. Prefer hhd800.com HD source, otherwise largest."""
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
        # Fall back to largest video file when no hhd800
        if all_video_candidates:
            all_video_candidates.sort(reverse=True)
            return (*all_video_candidates[0], False)
        return (0, -1, "", False)

    def _calc_prefetch_pieces(self, ti: lt.torrent_info, video_idx: int) -> tuple[int, int]:
        """Calculate prefetch piece range (first 2%)."""
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

    def set_liked(self, hash_str: str, liked: bool) -> None:
        """Update user_liked status for a work; liked works are protected from cache eviction."""
        with self.lock:
            if liked:
                self.liked_hashes.add(hash_str)
            else:
                self.liked_hashes.discard(hash_str)

    def _cache_score(self, info: dict[str, Any]) -> float:
        """Higher score = more valuable, less evictable.

        Combines: play history, completion, recency, value-per-GB, like status.
        """
        now = time.time()
        last_play = info.get("_last_play_time", 0)
        last_access = info["last_access"]
        progress = info.get("progress", 0)
        size = info.get("video_size", 1024)
        play_count = info.get("_play_count", 0)
        hash_str = info.get("hash", "")

        hours_since_play = (now - last_play) / 3600 if last_play else 9999
        heat = math.exp(-hours_since_play / 168)  # 7-day half-life

        # Play bonus: played torrents are an order of magnitude more valuable
        play_bonus = 1000.0 * play_count

        # Completion: 100% = 1000 pts, 50% = 500 pts
        completion_score = progress * 10

        # Value density: completed 6GB > incomplete 6GB
        size_gb = size / (1024 ** 3)
        value_per_gb = (play_bonus + completion_score) / max(size_gb, 0.1)

        score = value_per_gb * heat + play_bonus

        # Like bonus: liked works get strong protection; unliked get penalty
        if hash_str in self.liked_hashes:
            score += 5000.0
        else:
            score -= 2000.0

        return score

    def _punch_hole_middle_pieces(self, hash_str: str) -> int:
        """L4 downgrade: punch holes in non-head-tail pieces to free disk space.

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

        Soft limit: 95% of max. When exceeded, evict lowest-score torrents
        iteratively until below threshold. L1 (hot) torrents are protected at
        soft limit.

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

        evicted = 0
        while True:
            total = self._get_cache_size()
            if total <= soft_threshold:
                break

            with self.lock:
                candidates = [
                    (h, i) for h, i in self.torrents.items()
                    if force_evict_hot or self._get_tier(i) != "hot"
                ]

            if not candidates:
                log.error("cache eviction: no candidates available even under hard limit")
                break

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
                    continue

            # L2/L4 or punch-hole-insufficient L3 → full eviction
            log.info(
                f"evicting torrent {hash_str[:12]}... "
                f"(tier={tier}, score={self._cache_score(info):.0f}, "
                f"size={format_size(info.get('video_size', 0))})"
            )
            self.remove_torrent(hash_str)
            evicted += 1

        new_size = self._get_cache_size()
        log.info(
            f"cache eviction done: evicted={evicted}, current {format_size(new_size)}"
        )

    def add_torrent(self, magnet: str, prefetch: bool = False) -> dict[str, Any] | None:
        """Add a magnet link to the download queue."""
        hash_str = self._extract_hash(magnet)
        if not hash_str:
            return None

        with self.lock:
            existing = self.torrents.get(hash_str)
        if existing:
            existing["last_access"] = time.time()
            # Only re-run _on_metadata if tracker is missing (first time
            # metadata becomes available after a bare-hash add).
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
        # CRITICAL: default file priority to 0 (don't download) to prevent
        # libtorrent from creating full-size sparse files on metadata load.
        # We only enable the video file in _on_metadata after verifying disk.
        params.flags |= lt.torrent_flags.default_dont_download
        # Explicitly clear have_pieces to prevent libtorrent session from
        # restoring stale piece state (causes finished-state deadlock).
        params.have_pieces = []

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

        self._emit_event("cache.update", {"action": "add", "hash": hash_str})
        return info

    def _extract_hash(self, magnet: str) -> str | None:
        """Extract info hash from magnet link."""
        m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
        return m.group(1).lower() if m else None

    def _process_alerts(self) -> None:
        """Background thread: process libtorrent alert queue."""
        while not self._stop:
            for alert in self.session.pop_alerts():
                self._handle_alert(alert)
            time.sleep(0.5)

    def _handle_alert(self, alert: lt.alert) -> None:
        """Handle a single libtorrent alert."""
        if isinstance(alert, lt.metadata_received_alert):
            self._on_metadata(alert.handle)
        elif isinstance(alert, lt.torrent_checked_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    info = self.torrents[hash_str]
                    if info.get("tracker"):
                        info["tracker"]._bootstrap_from_filesystem()
            self._invalidate_status_cache(hash_str)
            self._emit_event("torrent.status", {"hash": hash_str, "state": "checked"})
        elif isinstance(alert, lt.torrent_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                info = self.torrents.get(hash_str)
            if not info:
                log.debug(
                    f"torrent_finished_alert ignored: {hash_str[:12]}... "
                    f"torrent not in engine (stale alert)"
                )
                return
            current_handle = info["handle"]
            if h.status().num_pieces != current_handle.status().num_pieces:
                log.debug(
                    f"torrent_finished_alert ignored: {hash_str[:12]}... "
                    f"handle mismatch (stale alert)"
                )
                return
            tracker = info.get("tracker")
            if tracker:
                tracker._bootstrap_from_filesystem()
            ready = tracker and tracker.head_ready()
            if ready:
                with self.lock:
                    if hash_str in self.torrents:
                        self.torrents[hash_str]["ready"] = True
            else:
                log.warning(
                    f"finished but head not ready: {hash_str[:12]}... "
                    f"disk scan shows holes, will readd on next stream request"
                )
            self._invalidate_status_cache(hash_str)
            self._emit_event("torrent.status", {"hash": hash_str, "state": "finished", "ready": ready})
        elif isinstance(alert, lt.piece_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    tracker = self.torrents[hash_str].get("tracker")
                    if tracker:
                        was_ready = tracker.head_ready()
                        tracker.on_piece_finished(alert.piece_index)
                        now_ready = tracker.head_ready()
                        if tracker.start_piece <= alert.piece_index <= tracker.end_piece:
                            log.info(
                                f"piece finished: {hash_str[:12]}... piece={alert.piece_index} "
                                f"verified={tracker.verified_count()}/{tracker.end_piece - tracker.start_piece + 1} "
                                f"head_ready={now_ready}"
                            )
                        if not was_ready and now_ready:
                            with self.lock:
                                if hash_str in self.torrents:
                                    self.torrents[hash_str]["ready"] = True
                            self._invalidate_status_cache(hash_str)
                            self._emit_event("torrent.head_ready", {"hash": hash_str})
        elif isinstance(alert, lt.hash_failed_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    tracker = self.torrents[hash_str].get("tracker")
                    if tracker:
                        tracker.on_hash_failed(alert.piece_index)
                        self._invalidate_status_cache(hash_str)

    def _on_metadata(self, handle: lt.torrent_handle) -> None:
        """When torrent metadata download completes, select video file and set priorities."""
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

        # Persist metadata so next playback doesn't need to re-find peers to download metadata
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
                            f"disk scan shows missing data, triggering readd"
                        )
                        self._readd_torrent(hash_str)
                        return
                else:
                    # No tracker yet — cannot verify disk state.
                    # Leave ready=False until bootstrap completes.
                    info["ready"] = False
                    return

        info["ready"] = True

    def _set_stream_window(
        self,
        h: lt.torrent_handle,
        info: dict[str, Any],
        time_sec: float,
        duration_sec: float,
        window_pcs: int = 30,
    ) -> bool:
        """Sliding-window strategy: urgent(7) inside window, retain(1) for downloaded, stop(0) for others.

        No longer reset all to 0 — that would drop downloaded pieces, causing repeated re-downloads,
        severely hurting experience. Instead retain downloaded pieces (priority=1), only set
        undownloaded pieces outside window to 0.
        """
        # CRITICAL: libtorrent 2.0 mmap storage creates a sparse file of full
        # torrent size on add_torrent, then reports finished even though no
        # data was actually written. In finished state libtorrent ignores
        # piece_priority / set_piece_deadline, causing playback deadlock.
        # force_recheck() is INEFFECTIVE for mmap storage — recheck reads
        # page-cache zeros and falsely marks pieces complete. The only reliable
        # fix is to remove the torrent + delete the sparse file, then re-add.
        status = h.status()
        if status.state == lt.torrent_status.finished:
            tracker = info.get("tracker")
            if tracker and tracker.verified_count() == 0:
                log.warning(
                    f"finished false-positive: {info['hash'][:12]}... "
                    f"triggering readd to clear stale state"
                )
                self._readd_torrent(info["hash"])
                return False

        if not status.has_metadata:
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

        # Read current priorities and apply incremental changes (avoid full reset dropping downloaded data)
        piece_prios = list(h.piece_priorities())
        changed = False

        # ---- moov / head_ready handling ----
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

        # ---- playback window ----
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
                # Inside window / inside moov: urgent
                if piece_prios[p] != 7:
                    piece_prios[p] = 7
                    h.set_piece_deadline(p, 0)
                    changed = True
                urgent_count += 1
            elif is_verified:
                # Downloaded: retain (priority=1), don't discard; can still seed when libtorrent is idle
                if piece_prios[p] != 1:
                    piece_prios[p] = 1
                    changed = True
                retain_count += 1
            else:
                # Not downloaded and outside window: stop
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
        """Start playback: after full reset only download necessary fragments (moov + minimal window), wait for frontend to report progress.

        No longer use tracker.request_head_tail() — that method doesn't reset other pieces' priorities,
        causing pieces under libtorrent default priority to keep downloading, resulting in "blooming everywhere".
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
        """Seek: shrink window to ±15 pieces, quickly locate target position."""
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
        """Normal playback sliding window: ±30 pieces (~2–4 minutes buffer), retain downloaded."""
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
        """Mark whether this torrent should keep cache after pause."""
        with self.lock:
            info = self.torrents.get(hash_str)
        if info:
            info["keep_cache"] = keep

    def pause_download(self, hash_str: str) -> bool:
        """Pause download: set all piece priorities to 0.
        Non-primary works (keep_cache=false) directly remove torrent and cache files."""
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
        """Resume download: re-set moov + current window."""
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

    def _invalidate_status_cache(self, hash_str: str) -> None:
        """Clear cached status for a torrent (call on state changes)."""
        with self._status_cache_lock:
            self._status_cache.pop(hash_str, None)

    def get_status(self, hash_str: str) -> dict[str, Any] | None:
        """Get playback and download status for specified torrent.

        Results are cached for 2 seconds to avoid repeated disk I/O
        (lseek/SEEK_HOLE, os.walk, MP4 moov scans) on every poll.
        Cache is invalidated automatically on piece/state changes.
        """
        # Check cache first
        with self._status_cache_lock:
            cached = self._status_cache.get(hash_str)
        if cached:
            data, ts = cached
            if time.time() - ts < self._status_cache_ttl:
                return data

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

        # Progress = real verified data ratio.
        progress = s.progress * 100
        if tracker:
            total_video_pieces = tracker.end_piece - tracker.start_piece + 1
            if total_video_pieces > 0:
                progress = (tracker.verified_count() / total_video_pieces) * 100

        # Persist progress for tiered cache scoring
        info["progress"] = progress

        checking_states = (lt.torrent_status.checking_files, lt.torrent_status.checking_resume_data)
        is_ready = info["ready"] and s.has_metadata and s.state not in checking_states

        result = {
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
            "piece_segments": tracker.get_lane_segments(10) if tracker else [],
            "tier": self._get_tier(info),
        }

        with self._status_cache_lock:
            self._status_cache[hash_str] = (result, time.time())
        return result

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
        """Get status list for all torrents."""
        with self.lock:
            hashes = list(self.torrents.keys())
        return [self.get_status(h) for h in hashes]

    def _readd_torrent(self, hash_str: str) -> dict[str, Any] | None:
        """Remove and re-add a torrent to clear libtorrent's stale finished state.

        Uses write_resume_data + cleared pieces instead of deleting .torrent,
        to preserve metadata while forcing libtorrent to re-evaluate disk state.
        This avoids the finished-state deadlock where libtorrent restores
        have_pieces from session cache.
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

        with self.lock:
            info = self.torrents.pop(hash_str, None)
        if not info:
            return None

        # Rate-limit readd to prevent infinite loops
        now = time.time()
        last_readd = info.get("_last_readd_time", 0)
        if now - last_readd < 60:
            log.warning(
                f"readd throttled: {hash_str[:12]}... "
                f"last={int(now - last_readd)}s ago"
            )
            return None
        saved["_last_readd_time"] = now

        h = info["handle"]
        ti = h.torrent_file()

        # Get resume data and clear all pieces to force re-download
        try:
            rd = h.write_resume_data()
            if b"pieces" in rd and rd[b"pieces"]:
                rd[b"pieces"] = b"\x00" * len(rd[b"pieces"])
                log.info(
                    f"_readd_torrent: cleared {len(rd[b'pieces'])} pieces "
                    f"for {hash_str[:12]}..."
                )
        except Exception as e:
            log.warning(f"_readd_torrent write_resume_data failed: {e}")
            rd = None

        try:
            self.session.remove_torrent(h, 0)
        except Exception as e:
            log.warning(f"_readd_torrent remove failed: {e}")

        # Give libtorrent time to fully remove torrent
        time.sleep(1.0)
        self.session.pop_alerts()

        # Re-add with cleared resume data + metadata
        save_path = os.path.join(self.cache_dir, hash_str)
        os.makedirs(save_path, exist_ok=True)

        params = lt.parse_magnet_uri(magnet)
        params.save_path = save_path
        params.flags &= ~lt.torrent_flags.auto_managed
        params.flags &= ~lt.torrent_flags.seed_mode
        params.flags &= ~lt.torrent_flags.paused
        params.flags |= lt.torrent_flags.default_dont_download
        params.have_pieces = []

        # Load metadata from cached .torrent if available
        torrent_path = os.path.join(save_path, f"{hash_str}.torrent")
        if os.path.exists(torrent_path):
            try:
                cached_ti = lt.torrent_info(torrent_path)
                if str(cached_ti.info_hash()) == hash_str:
                    params.ti = cached_ti
            except Exception as e:
                log.warning(f"_readd_torrent metadata load failed: {e}")

        if rd:
            try:
                resume_params = lt.read_resume_data(lt.bencode(rd))
                # Merge resume params (except ti) into params
                for key in ("download_limit", "upload_limit", "max_connections",
                            "max_uploads", "trackers", "tracker_tiers"):
                    if hasattr(resume_params, key) and getattr(resume_params, key):
                        setattr(params, key, getattr(resume_params, key))
            except Exception as e:
                log.warning(f"_readd_torrent read_resume_data failed: {e}")

        new_handle = self.session.add_torrent(params)

        new_info = {
            "handle": new_handle,
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
            self.torrents[hash_str] = new_info

        # If metadata was loaded, run _on_metadata immediately
        if params.ti is not None:
            self._on_metadata(new_handle)

        new_info.update(saved)
        tracker = new_info.get("tracker")
        if moov_start and moov_end and tracker:
            tracker.set_moov_range(moov_start, moov_end)

        log.info(f"readded torrent: {hash_str[:12]}... to clear finished deadlock")
        return new_info

    def remove_torrent(self, hash_str: str) -> bool:
        """Remove specified torrent and delete its cache files.

        First remove from torrents dict and call session.remove_torrent, letting libtorrent
        release file descriptors, then wait a short while before deleting directory to avoid (deleted) orphan files
        occupying disk space.
        """
        with self.lock:
            info = self.torrents.pop(hash_str, None)
        if not info:
            return False

        try:
            self.session.remove_torrent(info["handle"])
        except Exception as e:
            log.warning(f"remove_torrent session.remove failed: {e}")

        # Give libtorrent background threads time window to release file descriptors
        time.sleep(0.1)

        save_path = os.path.join(self.cache_dir, hash_str)
        for attempt in range(10):
            try:
                shutil.rmtree(save_path, ignore_errors=False)
                break
            except Exception as e:
                if attempt < 9:
                    log.warning(f"remove retry {attempt + 1} for {hash_str[:12]}: {e}")
                    time.sleep(0.1)
                else:
                    log.error(f"remove final error for {hash_str[:12]}: {e}")
                    # On last attempt ignore errors, at least clean up files that can be deleted
                    shutil.rmtree(save_path, ignore_errors=True)
        self._emit_event("cache.update", {"action": "remove", "hash": hash_str})
        return True

    def gc_orphaned_torrents(self, db_path: str) -> int:
        """Clean up orphaned torrents that exist on disk but have no matching title in database.

        Returns number of directories actually cleaned up.
        """
        if not os.path.isdir(self.cache_dir):
            return 0

        disk_hashes = set()
        for entry in os.scandir(self.cache_dir):
            if entry.is_dir() and len(entry.name) == 40:
                disk_hashes.add(entry.name)

        if not disk_hashes:
            return 0

        db_hashes: set[str] = set()
        try:
            conn = duckdb.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT DISTINCT magnet_hash FROM titles WHERE magnet_hash IS NOT NULL"
                ).fetchall()
                db_hashes = {h for (h,) in rows if h}
            finally:
                conn.close()
        except Exception as e:
            log.error(f"gc_orphaned_torrents: failed to query db: {e}")
            return 0

        orphaned = sorted(disk_hashes - db_hashes)
        removed = 0
        for hash_str in orphaned:
            try:
                # If already loaded into engine, use standard removal flow to release libtorrent handles
                with self.lock:
                    info = self.torrents.get(hash_str)
                if info:
                    self.remove_torrent(hash_str)
                else:
                    save_path = os.path.join(self.cache_dir, hash_str)
                    shutil.rmtree(save_path, ignore_errors=True)
                removed += 1
                log.info(f"gc_orphaned_torrents: removed orphan {hash_str[:12]}...")
            except Exception as e:
                log.warning(f"gc_orphaned_torrents: failed to remove {hash_str[:12]}...: {e}")

        if removed:
            log.info(f"gc_orphaned_torrents: removed {removed} orphan torrent(s)")
        return removed

    def _get_cache_size(self) -> int:
        """Calculate actual disk size (bytes) occupied by current cache directory."""
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
        """Background thread: periodically check and clean cache exceeding limits."""
        while not self._stop:
            time.sleep(CACHE_CLEAN_INTERVAL_SEC)
            if self._stop:
                break
            try:
                self._enforce_cache_limit()
            except Exception as e:
                log.error(f"periodic clean error: {e}")

            try:
                self._cleanup_orphaned()
            except Exception as e:
                log.error(f"periodic orphaned cleanup error: {e}")

            self._emit_event("cache.update", {"action": "periodic_clean"})

    def _cleanup_orphaned(self) -> None:
        """Clean up orphaned cache directories not in engine management list.

        Safety guard: if self.torrents is empty (just started, no torrents added yet),
        skip cleanup to avoid accidentally deleting existing user cache data.
        """
        if not os.path.exists(self.cache_dir):
            return
        with self.lock:
            known = set(self.torrents.keys())
        if not known:
            # Engine just started, torrents dict is empty, don't accidentally delete cache
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
        """Shutdown engine, stop background threads and release libtorrent session resources."""
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
