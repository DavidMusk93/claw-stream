#!/usr/bin/env python3
"""
cache-server.py — Unified BitTorrent cache server (local file direct play)

Architecture:
  1. libtorrent download engine → cache/torrent/<hash>/
  2. HTTP direct read local file → /stream/<hash> (Range supported)
  3. Prefer local cache when playing, start torrent download if no cache

Start:
  cd toolbox/actress-report && python3 cache-server.py
"""

import os, sys, json, re, time, threading, math, argparse, signal, shutil, subprocess, ipaddress, glob, datetime
from urllib.parse import unquote
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

import libtorrent as lt

from logger import get_logger, _ensure_log_dir

log = get_logger("cache-server")

# ── Config ──────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
REPORT_DIR = SCRIPT_DIR
WORKSPACE_DIR = os.path.dirname(os.path.dirname(REPORT_DIR))

MAX_CACHE_SIZE_GB = 20
PREFETCH_COUNT = 13
PREFETCH_PERCENT = 0.02
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".webm"}
SPAM_PATTERNS = [re.compile(p, re.I) for p in [
    r"game pack", r"996gg", r"^\d+\.txt$", r"^readme", r"\.url$", r"\.txt$"
]]

os.makedirs(CACHE_DIR, exist_ok=True)


def _scan_mp4_moov(path, max_read=16 * 1024 * 1024):
    """Scan MP4 file, find moov box end position.
    Handles extended size (size==1) and size==0 (box extends to EOF).
    For tail-moov files with large mdat, jumps to end of mdat to locate moov."""
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


def _mime_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
        ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
    }.get(ext, "video/mp4")


def find_video_state(hash_str):
    """Find video file and check if enough header data is downloaded for playback"""
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


def format_size(b):
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = math.floor(math.log(b) / math.log(1024))
    return f"{b / math.pow(1024, i):.1f} {units[i]}"


# ── BitTorrent engine ───────────────────────────────────
class TorrentEngine:
    def __init__(self, cache_dir, max_size_gb):
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
        self.torrents = {}
        self.lock = threading.Lock()

        self._stop = False
        self._alert_thread = threading.Thread(target=self._process_alerts, daemon=True)
        self._alert_thread.start()

    def _pick_video_file(self, ti):
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

    def _calc_prefetch_pieces(self, ti, video_idx):
        fs = ti.files()
        file_offset = fs.file_offset(video_idx)
        file_size = fs.file_size(video_idx)
        piece_length = ti.piece_length()
        prefetch_bytes = int(file_size * PREFETCH_PERCENT)
        start_piece = file_offset // piece_length
        end_piece = (file_offset + prefetch_bytes) // piece_length
        return start_piece, end_piece

    def _enforce_cache_limit(self):
        """LRU cache eviction: delete oldest torrent when exceeding 80% limit"""
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

    def add_torrent(self, magnet, prefetch=False):
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

    def _extract_hash(self, magnet):
        m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
        return m.group(1).lower() if m else None

    def _process_alerts(self):
        while not self._stop:
            for alert in self.session.pop_alerts():
                self._handle_alert(alert)
            time.sleep(0.5)

    def _handle_alert(self, alert):
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

    def _on_metadata(self, handle):
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

    def _apply_play_priority(self, h, info):
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

    def set_full_priority(self, hash_str):
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

    def get_status(self, hash_str):
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

    def get_all_status(self):
        with self.lock:
            hashes = list(self.torrents.keys())
        return [self.get_status(h) for h in hashes]

    def remove_torrent(self, hash_str):
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

    def _get_cache_size(self):
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

    def shutdown(self):
        self._stop = True
        self._alert_thread.join(timeout=5)


# ── HTTP handler ───────────────────────────────────────
def _is_local_client(address):
    """Only allow localhost and private IP to access admin endpoints"""
    try:
        ip = ipaddress.ip_address(address)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


class CacheHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, engine=None, **kwargs):
        self.engine = engine
        super().__init__(*args, **kwargs)

    def _today_password(self):
        """Daily rotating password"""
        d = datetime.datetime.now()
        return f"rn{d.strftime('%y%m%d')}{d.day % 2}"

    def _has_auth_cookie(self):
        cookie = self.headers.get("Cookie", "")
        return "claw_auth=ok" in cookie

    def _send_auth_page(self, msg=""):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Authentication Required</title>
<style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0a0a0a;color:#fff;font-family:system-ui,-apple-system,sans-serif}
.box{text-align:center;padding:24px}
input{padding:12px 20px;font-size:1rem;border-radius:8px;border:none;outline:none;width:220px;text-align:center;background:rgba(255,255,255,0.1);color:#fff}
input::placeholder{color:rgba(255,255,255,0.4)}
button{margin-top:16px;padding:10px 28px;border-radius:8px;border:none;background:#f97316;color:#fff;font-size:1rem;cursor:pointer;transition:background .2s}
button:hover{background:#ea580c}
#error{color:#ef4444;margin-top:10px;font-size:0.85rem;min-height:1.2em}
</style>
</head>
<body>
<div class="box">
<h2>&#128274; Enter password</h2>
<input type="password" id="pwd" placeholder="Password" onkeydown="if(event.key==='Enter')check()" autofocus>
<div id="error"></div>
<button onclick="check()">Enter</button>
</div>
<script>
function check(){
  var input=document.getElementById('pwd').value.trim();
  fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:input})})
  .then(function(r){return r.json();})
  .then(function(data){
    if(data.ok){
      location.href='/stream';
    }else{
      document.getElementById('error').textContent='Incorrect password';
    }
  })
  .catch(function(){document.getElementById('error').textContent='Incorrect password';});
}
</script>
</body>
</html>'''
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        msg = format % args
        if ".ts" in msg or ".m3u8" in msg or ".jpg" in msg or "stream" in msg:
            log.debug(f"serve: {msg.strip()}")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def translate_path(self, path):
        path = unquote(path)
        if path.startswith("/cache/"):
            return os.path.join(CACHE_DIR, path[7:])
        if path.startswith("/images/"):
            return os.path.join(REPORT_DIR, path[1:])
        if path == "/actresses-report.html":
            return os.path.join(WORKSPACE_DIR, "actresses-report.html")
        ws_path = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
        if os.path.exists(ws_path):
            return ws_path
        return super().translate_path(path)

    def _seek_priority(self, hash_str, start_byte, end_byte):
        """Set corresponding pieces to urgent based on Range request (priority boost + deadline)"""
        with self.engine.lock:
            info = self.engine.torrents.get(hash_str)
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

        # Calculate Range corresponding piece range (+-2 pieces buffer)
        start_piece = max(0, (file_offset + start_byte) // piece_length - 2)
        end_piece = min(num_pieces - 1, (file_offset + end_byte) // piece_length + 2)

        # Seek area pieces: priority 7 + urgent deadline
        # Ensure rest pieces at least 1 (smooth streaming)
        prios = [1] * num_pieces
        for p in range(start_piece, end_piece + 1):
            prios[p] = 7
            h.set_piece_deadline(p, 0)
        h.prioritize_pieces(prios)

    def _serve_video(self, hash_str):
        """Serve video stream directly from local file (Range support), trigger urgent download on seek"""
        path, real_size, head_ready, mime = find_video_state(hash_str)
        if not path:
            self.send_error(404, "Video not found")
            return

        # Ensure play priority is applied (tail pieces urgent for moov-in-tail MP4s)
        with self.engine.lock:
            info = self.engine.torrents.get(hash_str)
        if info:
            h = info["handle"]
            if h.status().has_metadata and info.get("prefetch"):
                # Switch from prefetch to play mode: boost tail pieces
                info["prefetch"] = False
                self.engine._apply_play_priority(h, info)
            elif h.status().has_metadata:
                self.engine._apply_play_priority(h, info)

        total_size = os.path.getsize(path)  # Logical size (sparse file may be large)
        range_hdr = self.headers.get("Range")

        if range_hdr:
            parts = range_hdr.replace("bytes=", "").split("-")
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else total_size - 1
            chunk_size = (end - start) + 1

            # When seek to undownloaded area, notify libtorrent urgent download
            self._seek_priority(hash_str, start, end)

            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", chunk_size)
            self.send_header("Content-Type", mime)
            self.end_headers()

            with open(path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    # Small chunk read, easier to detect hole boundary
                    buf = f.read(min(16384, remaining))
                    if not buf:
                        break
                    # Hole detection: all zeros means piece not downloaded
                    # Sending 0 causes browser parse failure, seek stuck
                    if not any(buf):
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
        else:
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", total_size)
            self.send_header("Content-Type", mime)
            self.end_headers()

            with open(path, "rb") as f:
                while True:
                    buf = f.read(16384)
                    if not buf:
                        break
                    if not any(buf):
                        break
                    self.wfile.write(buf)

    def do_GET(self):
        path = unquote(self.path)

        # Video stream (direct read from local file)
        stream_match = re.match(r"^/stream/([a-f0-9]{40})$", path, re.I)
        if stream_match:
            self._serve_video(stream_match.group(1).lower())
            return

        # Torrent status query
        status_match = re.match(r"^/torrent/status/([a-f0-9]{40})$", path, re.I)
        if status_match:
            hash_str = status_match.group(1).lower()
            status = self.engine.get_status(hash_str)
            if not status:
                self.send_error(404, "Not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode())
            return

        # Cache check (head ready required to play)
        check_match = re.match(r"^/api/check/([a-f0-9]{40})$", path, re.I)
        if check_match:
            hash_str = check_match.group(1).lower()
            local_path, local_size, head_ready, mime = find_video_state(hash_str)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "hash": hash_str,
                "cached": local_size > 1024 * 1024,
                "head_ready": head_ready,
                "path": local_path,
                "size": local_size,
                "mime": mime,
            }).encode())
            return

        # All cache status
        if path == "/api/cache":
            items = self.engine.get_all_status()
            total_disk = self.engine._get_cache_size()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "totalSize": total_disk,
                "maxSize": self.engine.max_size_bytes,
                "itemCount": len(items),
                "items": items,
            }).encode())
            return

        # Auth entry: /stream and /
        if path == "/stream" or path == "/":
            if self._has_auth_cookie():
                report_path = os.path.join(WORKSPACE_DIR, "actresses-report.html")
                if os.path.exists(report_path):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    with open(report_path, "rb") as f:
                        self.wfile.write(f.read())
                else:
                    self.send_error(404, "actresses-report.html not found")
            else:
                self._send_auth_page()
            return

        # Log list/view
        if path == "/api/logs" or path.startswith("/api/logs/"):
            subpath = path[len("/api/logs"):].lstrip("/")
            log_dir = _ensure_log_dir(os.environ.get("LOG_DIR", os.path.join(SCRIPT_DIR, "logs")))
            target = os.path.normpath(os.path.join(log_dir, subpath))
            if not target.startswith(os.path.normpath(log_dir)):
                self.send_error(403, "Forbidden")
                return
            if os.path.isdir(target):
                entries = []
                for entry in sorted(os.listdir(target)):
                    fp = os.path.join(target, entry)
                    st = os.stat(fp)
                    entries.append({
                        "name": entry,
                        "is_dir": os.path.isdir(fp),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
                self._send_json({"path": subpath or "/", "entries": entries})
                return
            elif os.path.isfile(target):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                with open(target, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self._send_json({"path": subpath or "/", "entries": []})
                return

        # Metrics endpoint
        if path == "/api/metrics":
            items = self.engine.get_all_status()
            total_disk = self.engine._get_cache_size()
            completed = sum(1 for i in items if i.get("progress", 0) >= 99.9)
            self._send_json({
                "torrents": {
                    "total": len(items),
                    "completed": completed,
                    "downloading": len(items) - completed,
                },
                "cache": {
                    "used_bytes": total_disk,
                    "used_human": format_size(total_disk),
                    "max_bytes": self.engine.max_size_bytes,
                    "max_human": format_size(self.engine.max_size_bytes),
                },
                "uptime": time.time() - getattr(self, "_server_start", time.time()),
            })
            return

        super().do_GET()

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        path = unquote(self.path)

        # ── Frontend error reporting ──
        if path == "/api/log":
            content_length = int(self.headers.get("Content-Level", 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode("utf-8", errors="replace")
                try:
                    data = json.loads(body)
                    frontend_log = get_logger("frontend")
                    level = data.get("level", "error")
                    msg = data.get("message", "frontend error")
                    extra = {k: v for k, v in data.items() if k not in ("level", "message")}
                    getattr(frontend_log, level, frontend_log.error)(msg, extra=extra)
                except Exception as e:
                    log.error(f"frontend log parse error: {e}")
            self._send_json({"ok": True})
            return

        # ── One-click refresh: re-fetch data and regenerate report ──
        if path == "/api/regenerate":
            if not _is_local_client(self.client_address[0]):
                self.send_error(403, "Forbidden: local access only")
                return

            script_path = os.path.join(SCRIPT_DIR, "refresh.sh")
            if not os.path.exists(script_path):
                self.send_error(500, "refresh.sh not found")
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            # Stream output: send running status first
            self.wfile.write(json.dumps({"status": "running", "message": "Refreshing..."}).encode())
            self.wfile.write(b"\n")

            try:
                proc = subprocess.run(
                    ["bash", script_path],
                    cwd=SCRIPT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=600,  # 10 min timeout
                )
                result = {
                    "status": "done" if proc.returncode == 0 else "error",
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
                self.wfile.write(json.dumps(result).encode())
            except subprocess.TimeoutExpired:
                self.wfile.write(json.dumps({"status": "error", "message": "Refresh timeout (>10min)"}).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
            return

        if path == "/torrent/add":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            try:
                data = json.loads(body)
                magnet = data.get("magnet")
                if not magnet:
                    self.send_error(400, "Missing magnet")
                    return

                prefetch = data.get("prefetch", False)
                info = self.engine.add_torrent(magnet, prefetch=prefetch)
                if not info:
                    self.send_error(400, "Invalid magnet")
                    return

                hash_str = info["hash"]

                if not prefetch:
                    self.engine.set_full_priority(hash_str)

                h = info["handle"]
                for _ in range(20):
                    if h.status().has_metadata:
                        break
                    time.sleep(0.5)

                status = self.engine.get_status(hash_str)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "hash": hash_str,
                    "status": "added",
                    "ready": status["ready"] if status else False,
                    "peers": status["peers"] if status else 0,
                    "progress": status["progress"] if status else 0,
                }).encode())
            except Exception as e:
                self.send_error(400, str(e))
            return

        self.send_error(405, "Method not allowed")

    def do_DELETE(self):
        path = unquote(self.path)

        cache_match = re.match(r"^/api/cache/([a-f0-9]{40})$", path, re.I)
        if cache_match:
            hash_str = cache_match.group(1).lower()
            success = self.engine.remove_torrent(hash_str)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"deleted": success}).encode())
            return

        self.send_error(405, "Method not allowed")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Unified BitTorrent cache server")
    parser.add_argument("--port", type=int, default=8765, help="Listen port (default: 8765)")
    parser.add_argument("--max-size", type=int, default=20, help="Max cache size GB (default: 20)")
    args = parser.parse_args()

    engine = TorrentEngine(CACHE_DIR, args.max_size)

    def signal_handler(sig, frame):
        log.info("shutdown: stopping server...")
        engine.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    os.chdir(WORKSPACE_DIR)

    def handler_factory(*args, **kwargs):
        h = CacheHandler(*args, engine=engine, **kwargs)
        h._server_start = time.time()
        return h

    server = ThreadedHTTPServer(("0.0.0.0", args.port), handler_factory)

    log.info("=" * 60)
    log.info(f"Cache Server started at http://localhost:{args.port}/")
    log.info(f"Cache directory: {CACHE_DIR}")
    log.info(f"Log directory: {os.environ.get('LOG_DIR', os.path.join(SCRIPT_DIR, 'logs'))}")
    log.info(f"Max cache size: {args.max_size}GB")
    log.info(f"Engine: libtorrent {lt.version}")
    log.info("=" * 60)
    log.info("Press Ctrl+C to stop")
    log.info("=" * 60)

    server.serve_forever()


if __name__ == "__main__":
    main()
