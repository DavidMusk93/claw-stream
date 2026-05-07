from __future__ import annotations

import math
import os
import re
import shutil
import threading
import time
from typing import Any

import libtorrent as lt

from logger import get_logger

log = get_logger("torrent-engine")

# ── Config ──────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_SCRIPT_DIR, "cache", "torrent")

MAX_CACHE_SIZE_GB = 20
PREFETCH_COUNT = 13
PREFETCH_PERCENT = 0.02
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".webm"}
SPAM_PATTERNS = [re.compile(p, re.I) for p in [
    r"game pack", r"996gg", r"^\d+\.txt$", r"^readme", r"\.url$", r"\.txt$"
]]

os.makedirs(CACHE_DIR, exist_ok=True)


def _scan_mp4_moov(path: str, max_read: int = 16 * 1024 * 1024) -> int:
    """扫描 MP4 文件，查找 moov box 的结束位置。

    处理扩展大小（size==1）和 size==0（box 延伸至 EOF）的情况。
    对于尾部 moov 文件，如果 mdat 很大，则跳转到 mdat 末尾定位 moov。
    """
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
                    # Box extends to end of file
                    mdat_end = file_size
                    break
                if size == 1:
                    # Extended size in next 8 bytes
                    if offset + 16 > len(data):
                        break
                    size = int.from_bytes(data[offset+8:offset+16], "big")
                    if size > 100 * 1024 * 1024 * 1024:
                        break
                    if box_type == b"mdat":
                        mdat_end = offset + size
                        break
                elif size < 8 or size > 100 * 1024 * 1024:
                    break
                if box_type == b"moov":
                    return offset + size
                offset += size

            # If we found a large mdat, moov is likely right after it (tail-moov)
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
                        return (mdat_end - 1024 if mdat_end >= 1024 else 0) + moov_idx - 4 + box_size
    except Exception:
        pass
    return 0


def _mime_type(path: str) -> str:
    """根据文件扩展名返回对应的 MIME 类型。"""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
        ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
    }.get(ext, "video/mp4")


def find_video_state(hash_str: str) -> tuple[str | None, int, bool, str]:
    """查找视频文件并检查是否已下载足够的头部数据以供播放。

    返回: (文件路径, 实际磁盘大小, 头部是否就绪, MIME 类型)
    """
    dir_path = os.path.join(CACHE_DIR, hash_str)
    if not os.path.exists(dir_path):
        return None, 0, False, "video/mp4"
    best = None
    best_size = 0
    best_logic = 0
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
                    best_logic = st.st_size
            except OSError:
                pass
    if not best or best_size < 1024 * 1024:
        return best, best_size, False, _mime_type(best or "")

    mime = _mime_type(best)
    ext = os.path.splitext(best)[1].lower()

    # MP4/MOV: need moov atom downloaded
    if ext in (".mp4", ".m4v", ".mov"):
        moov_end = _scan_mp4_moov(best)
        if moov_end == 0:
            # moov not in head, try find in tail (scan last 128MB — moov may be
            # far from end if mdat uses extended size)
            try:
                tail_scan_size = min(128 * 1024 * 1024, best_logic)
                tail_offset = max(0, best_logic - tail_scan_size)
                with open(best, "rb") as f:
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
                            moov_end = tail_offset + moov_idx - 4 + box_size
                        else:
                            return best, best_size, False, mime
                    else:
                        return best, best_size, False, mime
            except Exception:
                return best, best_size, False, mime

        # Confirm moov_end in downloaded area (non-zero bytes around moov end)
        head_ready = False
        try:
            with open(best, "rb") as f:
                f.seek(max(0, moov_end - 1024))
                tail = f.read(1024)
                if len(tail) == 1024 and not all(b == 0 for b in tail):
                    head_ready = True
        except Exception:
            pass
        return best, best_size, head_ready, mime

    # MKV/WEBM: need first 5MB downloaded for header parsing
    if ext in (".mkv", ".webm"):
        return best, best_size, best_size >= 5 * 1024 * 1024, mime

    # Other formats: need first 10MB
    return best, best_size, best_size >= 10 * 1024 * 1024, mime


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
        self.session.apply_settings(settings)

        # hash -> { handle, magnet, added_at, last_access, video_idx, video_path, video_size }
        self.torrents: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

        self._stop = False
        self._alert_thread = threading.Thread(target=self._process_alerts, daemon=True)
        self._alert_thread.start()

    def _pick_video_file(self, ti: lt.torrent_info) -> tuple[int, int, str]:
        """从 torrent 文件中挑选最合适的视频文件。"""
        fs = ti.files()
        candidates = []
        hhd800_candidates = []
        for idx in range(fs.num_files()):
            name = fs.file_path(idx)
            size = fs.file_size(idx)
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            if any(p.search(name) for p in SPAM_PATTERNS):
                continue
            # hhd800.com@ prefix indicates main video file (not spam)
            if "hhd800" in name.lower() or "hdd800" in name.lower():
                hhd800_candidates.append((size, idx, name))
            candidates.append((size, idx, name))
        # Prefer hhd800/hdd800 main video files
        if hhd800_candidates:
            hhd800_candidates.sort(reverse=True)
            return hhd800_candidates[0]
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0]
        # Fallback: any file
        for idx in range(fs.num_files()):
            candidates.append((fs.file_size(idx), idx, fs.file_path(idx)))
        candidates.sort(reverse=True)
        return candidates[0] if candidates else (0, 0, "")

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
        }
        with self.lock:
            self.torrents[hash_str] = info
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
            # After file verification, reapply play priority
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    info = self.torrents[hash_str]
                    # If play mode (not prefetch), reset head urgent
                    if not info.get("prefetch"):
                        self._apply_play_priority(h, info)
        elif isinstance(alert, lt.torrent_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    self.torrents[hash_str]["ready"] = True

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
        info["video_idx"] = idx
        info["video_path"] = os.path.join(info["handle"].status().save_path, name)
        info["video_size"] = size

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
            # Default: all pieces priority 0 (set head urgent when playing)
            piece_prios = [0] * num_pieces
            handle.prioritize_pieces(piece_prios)
            log.info(f"added: {name} ({format_size(size)})")

        info["ready"] = True

    def _apply_play_priority(self, h: lt.torrent_handle, info: dict[str, Any]) -> bool:
        """Apply play priority: head + tail urgent, rest slow (stream while download)"""
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
        start_piece = file_offset // piece_length
        end_piece = (file_offset + fs.file_size(idx)) // piece_length

        # Head pieces: urgent deadline + priority 7 (for moov-in-head & first frame)
        head_count = min(30, num_pieces)
        for p in range(start_piece, min(start_piece + head_count, num_pieces)):
            h.set_piece_deadline(p, 0)

        # Tail pieces: priority 7 (for moov-in-tail)
        tail_count = min(30, num_pieces)

        piece_prios = [1] * num_pieces
        # Head
        for p in range(start_piece, min(start_piece + head_count, num_pieces)):
            piece_prios[p] = 7
        # Tail
        for p in range(max(start_piece, end_piece - tail_count + 1), min(end_piece + 1, num_pieces)):
            piece_prios[p] = 7

        h.prioritize_pieces(piece_prios)

        # Do NOT use sequential_download — it forces head-to-tail order,
        # which delays tail-moov download for hours. Piece priority alone
        # lets libtorrent fetch head + tail simultaneously.
        h.set_sequential_download(False)

        log.info(f"play priority: {info['hash'][:12]}... head={head_count}pcs tail={tail_count}pcs seq=false")
        return True

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

    def get_status(self, hash_str: str) -> dict[str, Any] | None:
        """获取指定 torrent 的播放和下载状态。"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return None

        h = info["handle"]
        s = h.status()
        local_path, local_size, head_ready, mime = find_video_state(hash_str)

        return {
            "hash": hash_str,
            "name": s.name,
            "ready": info["ready"] and s.has_metadata,
            "cached": local_size > 1024 * 1024,
            "head_ready": head_ready,
            "peers": s.num_peers,
            "progress": s.progress * 100,
            "download_rate": s.download_rate,
            "upload_rate": s.upload_rate,
            "video_file": os.path.basename(info["video_path"]) if info["video_path"] else None,
            "video_size": info["video_size"],
            "local_size": local_size,
            "mime": mime,
            "state": str(s.state),
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

    def shutdown(self) -> None:
        """关闭引擎，停止 alert 处理线程。"""
        self._stop = True
        self._alert_thread.join(timeout=5)
