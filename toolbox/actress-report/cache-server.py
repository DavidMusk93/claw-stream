#!/usr/bin/env python3
"""
cache-server.py — 一体化 BitTorrent 缓存服务器（本地文件直接播放版）

架构：
  1. libtorrent 下载引擎 → cache/torrent/<hash>/
  2. HTTP 直接读取本地文件 → /stream/<hash>（Range 支持）
  3. 播放时优先本地缓存，无缓存则启动 torrent 下载

启动：
  cd toolbox/actress-report && python3 cache-server.py
"""

import os, sys, json, re, time, threading, math, argparse, signal, shutil, subprocess, ipaddress, glob, datetime
from urllib.parse import unquote
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

import libtorrent as lt

from logger import get_logger, _ensure_log_dir

log = get_logger("cache-server")

# ── 配置 ──────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
REPORT_DIR = SCRIPT_DIR
WORKSPACE_DIR = os.path.dirname(os.path.dirname(REPORT_DIR))

MAX_CACHE_SIZE_GB = 20
PREFETCH_COUNT = 13
PREFETCH_PERCENT = 0.02
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".webm"}
SPAM_PATTERNS = [re.compile(p, re.I) for p in [
    r"游戏大全", r"996gg", r"hhd800", r"^\d+\.txt$", r"^readme", r"\.url$", r"\.txt$"
]]

os.makedirs(CACHE_DIR, exist_ok=True)


def _scan_mp4_moov(path, max_read=16 * 1024 * 1024):
    """扫描 MP4 文件，找到 moov box 的结束位置"""
    try:
        with open(path, "rb") as f:
            data = f.read(max_read)
            offset = 0
            while offset < len(data) - 8:
                size = int.from_bytes(data[offset:offset+4], "big")
                box_type = data[offset+4:offset+8]
                if size == 0 or size > 100 * 1024 * 1024:
                    break
                if box_type == b"moov":
                    return offset + size
                offset += size
    except Exception:
        pass
    return 0


def find_video_state(hash_str):
    """查找视频文件并检查 moov 是否完整下载"""
    dir_path = os.path.join(CACHE_DIR, hash_str)
    if not os.path.exists(dir_path):
        return None, 0, False
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
        return best, best_size, False

    # 扫描 moov 位置
    moov_end = _scan_mp4_moov(best)
    if moov_end == 0:
        # moov 不在头部，尝试在尾部查找
        try:
            with open(best, "rb") as f:
                f.seek(max(0, best_logic - 1024 * 1024))
                tail = f.read()
                if b"moov" in tail:
                    # moov 在尾部，需要完整下载才能播放
                    moov_end = best_logic
                else:
                    return best, best_size, False
        except Exception:
            return best, best_size, False

    # 确认 moov_end 在已下载区域（非 0）
    head_ready = False
    try:
        with open(best, "rb") as f:
            f.seek(moov_end - 1024)
            tail = f.read(1024)
            if len(tail) == 1024 and not all(b == 0 for b in tail):
                head_ready = True
    except Exception:
        pass
    return best, best_size, head_ready


def format_size(b):
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = math.floor(math.log(b) / math.log(1024))
    return f"{b / math.pow(1024, i):.1f} {units[i]}"


# ── BitTorrent 引擎 ───────────────────────────────────
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
        for idx in range(fs.num_files()):
            name = fs.file_path(idx)
            size = fs.file_size(idx)
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            if any(p.search(name) for p in SPAM_PATTERNS):
                continue
            candidates.append((size, idx, name))
        if not candidates:
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

    def add_torrent(self, magnet, prefetch=False):
        hash_str = self._extract_hash(magnet)
        if not hash_str:
            return None

        with self.lock:
            if hash_str in self.torrents:
                info = self.torrents[hash_str]
                info["last_access"] = time.time()
                return info

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
            # 文件校验完成后，重新应用播放优先级
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    info = self.torrents[hash_str]
                    # 如果是播放模式（非预缓存），重新设置头部 urgent
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
            # 预加载模式：前 2% pieces 优先级 4，其余 0
            start, end = self._calc_prefetch_pieces(ti, idx)
            piece_prios = [0] * num_pieces
            for p in range(start, min(end + 1, num_pieces)):
                piece_prios[p] = 4
            handle.prioritize_pieces(piece_prios)
            log.info(f"prefetch: {name} ({format_size(size)}) pieces {start}-{end}")
        else:
            # 默认：所有 pieces 优先级 0（等播放时再设置头部 urgent）
            piece_prios = [0] * num_pieces
            handle.prioritize_pieces(piece_prios)
            log.info(f"added: {name} ({format_size(size)})")

        info["ready"] = True

    def _apply_play_priority(self, h, info):
        """应用播放优先级：头部 urgent，其余慢速（边下边播）"""
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

        # 头部 pieces: urgent deadline + 优先级 7
        # moov 最大可达 12MB，需要至少 6 pieces，保险起见下载 30pcs (~60MB)
        head_count = min(30, num_pieces)
        for p in range(start_piece, min(start_piece + head_count, num_pieces)):
            h.set_piece_deadline(p, 0)

        # 头部 pieces: 优先级 7
        # 其余 pieces: 优先级 1（慢速下载，边下边播不卡顿）
        piece_prios = [1] * num_pieces
        for p in range(start_piece, min(start_piece + head_count, num_pieces)):
            piece_prios[p] = 7
        h.prioritize_pieces(piece_prios)

        # 开启顺序下载，确保按顺序填充空洞
        h.set_sequential_download(True)

        log.info(f"play priority: {info['hash'][:12]}... head={head_count}pcs, seq=true")
        return True

    def set_full_priority(self, hash_str):
        """播放模式：头部 urgent，其余暂停——确保头部先就绪"""
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
        local_path, local_size, head_ready = find_video_state(hash_str)

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


# ── HTTP 处理器 ───────────────────────────────────────
def _is_local_client(address):
    """只允许本地回环和私有 IP 访问管理端点"""
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
        """动态密码: rnYYmmdd{day%2}  例: rn2605060"""
        d = datetime.datetime.now()
        return f"rn{d.strftime('%y%m%d')}{d.day % 2}"

    def _has_auth_cookie(self):
        cookie = self.headers.get("Cookie", "")
        return "claw_auth=ok" in cookie

    def _send_auth_page(self, msg=""):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = b'''<!DOCTYPE html>
<html lang="zh-CN">
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
.hint{color:rgba(255,255,255,0.35);margin-top:16px;font-size:0.75rem}
</style>
</head>
<body>
<div class="box">
<h2>&#128274; \u8bf7\u8f93\u5165\u5bc6\u7801</h2>
<input type="password" id="pwd" placeholder="\u5bc6\u7801" onkeydown="if(event.key==='Enter')check()" autofocus>
<div id="error"></div>
<button onclick="check()">\u8fdb\u5165</button>
<div class="hint">\u683c\u5f0f: rn + \u5e74\u6708\u65e5 + \u5947\u5076 (1/0)</div>
</div>
<script>
function getPwd(){
  var d=new Date();
  var yy=(d.getFullYear()%100).toString().padStart(2,'0');
  var mm=(d.getMonth()+1).toString().padStart(2,'0');
  var dd=d.getDate().toString().padStart(2,'0');
  return 'rn'+yy+mm+dd+(d.getDate()%2);
}
function check(){
  var input=document.getElementById('pwd').value.trim();
  if(input===getPwd()){
    document.cookie='claw_auth=ok; path=/; max-age=86400';
    location.href='/stream';
  }else{
    document.getElementById('error').textContent='\u5bc6\u7801\u9519\u8bef';
  }
}
</script>
</body>
</html>'''
        self.wfile.write(html)

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
        """根据 Range 请求设置对应 pieces 为 urgent（优先级提升 + 截止时间）"""
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

        # 计算 Range 对应的 piece 范围（加前后各 2 个 piece 缓冲）
        start_piece = max(0, (file_offset + start_byte) // piece_length - 2)
        end_piece = min(num_pieces - 1, (file_offset + end_byte) // piece_length + 2)

        # seek 区域 pieces: 优先级提到 7 + urgent deadline
        # 确保其余 pieces 至少为 1（边下边播不卡顿）
        prios = [1] * num_pieces
        for p in range(start_piece, end_piece + 1):
            prios[p] = 7
            h.set_piece_deadline(p, 0)
        h.prioritize_pieces(prios)

    def _serve_video(self, hash_str):
        """直接从本地文件提供视频流（支持 Range），seek 时触发 urgent 下载"""
        path, real_size, head_ready = find_video_state(hash_str)
        if not path or not head_ready:
            self.send_error(404, "Video head not ready yet")
            return

        total_size = os.path.getsize(path)  # 逻辑大小（稀疏文件可能很大）
        range_hdr = self.headers.get("Range")

        if range_hdr:
            parts = range_hdr.replace("bytes=", "").split("-")
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else total_size - 1
            chunk_size = (end - start) + 1

            # Seek 到未下载区域时，通知 libtorrent urgent 下载
            self._seek_priority(hash_str, start, end)

            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", chunk_size)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()

            with open(path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    # 小块读取，更容易检测空洞边界
                    buf = f.read(min(16384, remaining))
                    if not buf:
                        break
                    # 空洞检测：全 0 说明该 piece 未下载
                    # 发送 0 会导致浏览器解析失败、seek 卡住
                    if not any(buf):
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
        else:
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", total_size)
            self.send_header("Content-Type", "video/mp4")
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

        # 视频流（直接从本地文件读取）
        stream_match = re.match(r"^/stream/([a-f0-9]{40})$", path, re.I)
        if stream_match:
            self._serve_video(stream_match.group(1).lower())
            return

        # torrent 状态查询
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

        # 缓存检查（头部就绪才能播放）
        check_match = re.match(r"^/api/check/([a-f0-9]{40})$", path, re.I)
        if check_match:
            hash_str = check_match.group(1).lower()
            local_path, local_size, head_ready = find_video_state(hash_str)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "hash": hash_str,
                "cached": local_size > 1024 * 1024,
                "head_ready": head_ready,
                "path": local_path,
                "size": local_size,
            }).encode())
            return

        # 所有缓存状态
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

        # 认证入口: /stream 和 /
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

        # 日志列表/查看
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

        # 指标端点
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

        # ── 前端错误上报 ──
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

        # ── 一键刷新：重新抓取数据并重排生成报告 ──
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

            # 流式输出：先发送 running 状态
            self.wfile.write(json.dumps({"status": "running", "message": "开始刷新..."}).encode())
            self.wfile.write(b"\n")

            try:
                proc = subprocess.run(
                    ["bash", script_path],
                    cwd=SCRIPT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=600,  # 10 分钟超时
                )
                result = {
                    "status": "done" if proc.returncode == 0 else "error",
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
                self.wfile.write(json.dumps(result).encode())
            except subprocess.TimeoutExpired:
                self.wfile.write(json.dumps({"status": "error", "message": "刷新超时 (>10min)"}).encode())
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
    parser = argparse.ArgumentParser(description="一体化 BitTorrent 缓存服务器")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    parser.add_argument("--max-size", type=int, default=20, help="最大缓存大小 GB (默认: 20)")
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
