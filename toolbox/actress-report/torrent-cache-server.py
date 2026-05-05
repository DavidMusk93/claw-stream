#!/usr/bin/env python3
"""
torrent-cache-server.py — 一体化 BitTorrent 缓存流服务器

基于 libtorrent，提供：
1. BitTorrent 下载引擎（稀疏文件、精细 piece 控制）
2. HTTP 流式播放（Range 请求，直接从稀疏文件读取）
3. 缓存管理（以文件为单位、LRU 淘汰、punch hole）

启动策略：
  - 自动加载所有 magnet
  - 最新 13 个视频：前 2% pieces 设为中等优先级
  - 其他视频：不下载（优先级 0）

播放策略：
  - POST /torrent/add 触发全速下载（所有 pieces 优先级 7）
  - 等待 8 秒后返回 ready（前几个 pieces 已就绪）
  - 浏览器直接从文件流播放

启动：
  cd toolbox/actress-report && python3 torrent-cache-server.py
"""

import os, sys, json, re, time, threading, math, argparse, signal
from urllib.parse import unquote
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

import libtorrent as lt

# ── 配置 ──────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
REPORT_DIR = SCRIPT_DIR
WORKSPACE_DIR = os.path.dirname(os.path.dirname(REPORT_DIR))

MAX_CACHE_SIZE_GB = 20
PREFETCH_COUNT = 13       # 启动时预加载多少个视频
PREFETCH_PERCENT = 0.02   # 预加载百分比（2%）
PLAY_READY_WAIT = 8       # 点击播放后等待多少秒认为"就绪"
PLAY_READY_BYTES = 10 * 1024 * 1024  # 或至少下载 10MB 认为就绪

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".webm"}
SPAM_PATTERNS = [re.compile(p, re.I) for p in [
    r"游戏大全", r"996gg", r"hhd800", r"^\d+\.txt$", r"^readme", r"\.url$", r"\.txt$"
]]

os.makedirs(CACHE_DIR, exist_ok=True)

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
        
        # 启动 alert 处理线程
        self._stop = False
        self._alert_thread = threading.Thread(target=self._process_alerts, daemon=True)
        self._alert_thread.start()
    
    def _pick_video_file(self, ti):
        """选择最佳视频文件（最大、排除垃圾）"""
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
            #  fallback：选最大的任何文件
            for idx in range(fs.num_files()):
                candidates.append((fs.file_size(idx), idx, fs.file_path(idx)))
        candidates.sort(reverse=True)
        return candidates[0] if candidates else (0, 0, "")
    
    def _calc_prefetch_pieces(self, ti, video_idx):
        """计算预加载的 pieces（前 2%）"""
        fs = ti.files()
        file_offset = fs.file_offset(video_idx)
        file_size = fs.file_size(video_idx)
        piece_length = ti.piece_length()
        
        prefetch_bytes = int(file_size * PREFETCH_PERCENT)
        start_piece = file_offset // piece_length
        end_piece = (file_offset + prefetch_bytes) // piece_length
        return start_piece, end_piece
    
    def add_torrent(self, magnet, prefetch=False):
        """添加/获取种子"""
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
            # 先不设置文件优先级，等 metadata 就绪后设置
            
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
        """后台线程：处理 libtorrent alerts"""
        while not self._stop:
            for alert in self.session.pop_alerts():
                self._handle_alert(alert)
            time.sleep(0.5)
    
    def _handle_alert(self, alert):
        """处理单个 alert"""
        if isinstance(alert, lt.metadata_received_alert):
            self._on_metadata(alert.handle)
        elif isinstance(alert, lt.torrent_finished_alert):
            h = alert.handle
            hash_str = str(h.info_hash())
            with self.lock:
                if hash_str in self.torrents:
                    self.torrents[hash_str]["ready"] = True
    
    def _on_metadata(self, handle):
        """metadata 就绪后：选择视频文件、设置优先级"""
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
        
        # 设置文件优先级：只下载视频文件
        file_prios = [0] * fs.num_files()
        file_prios[idx] = 7
        handle.prioritize_files(file_prios)
        
        # 根据 prefetch 标志设置 piece 优先级
        num_pieces = ti.num_pieces()
        if info.get("prefetch"):
            # 预加载模式：前 2% pieces 优先级 4
            start, end = self._calc_prefetch_pieces(ti, idx)
            piece_prios = [0] * num_pieces
            for p in range(start, min(end + 1, num_pieces)):
                piece_prios[p] = 4
            handle.prioritize_pieces(piece_prios)
            print(f"[torrent] metadata ready (prefetch): {name} ({size/1024/1024/1024:.1f}GB) pieces {start}-{end}")
        else:
            # 默认：所有 pieces 优先级 0（不下载）
            piece_prios = [0] * num_pieces
            handle.prioritize_pieces(piece_prios)
            print(f"[torrent] metadata ready: {name} ({size/1024/1024/1024:.1f}GB)")
        
        info["ready"] = True
    
    def set_full_priority(self, hash_str):
        """设置某个 torrent 为全速下载（所有 pieces 优先级 7）"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        h = info["handle"]
        if not h.status().has_metadata:
            return False
        
        ti = h.torrent_file()
        fs = ti.files()
        idx = info["video_idx"]
        
        # 确保文件优先级正确
        file_prios = [0] * fs.num_files()
        file_prios[idx] = 7
        h.prioritize_files(file_prios)
        
        # 设置所有 pieces 为最高优先级
        num_pieces = ti.num_pieces()
        piece_prios = [7] * num_pieces
        h.prioritize_pieces(piece_prios)
        
        # 紧急下载头部 pieces（前 10MB = 约 5 个 pieces）
        piece_length = ti.piece_length()
        file_offset = fs.file_offset(idx)
        head_pieces = min(10, num_pieces)
        start_piece = file_offset // piece_length
        for p in range(start_piece, min(start_piece + head_pieces, num_pieces)):
            h.set_piece_deadline(p, 0)
        
        info["last_access"] = time.time()
        print(f"[torrent] full speed: {hash_str} (head pieces {start_piece}-{start_piece + head_pieces - 1} urgent)")
        return True
    
    def set_prefetch_priority(self, hash_str):
        """设置某个 torrent 为预加载模式（前 2% pieces 优先级 4）"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return False
        h = info["handle"]
        if not h.status().has_metadata:
            return False
        
        ti = h.torrent_file()
        idx = info["video_idx"]
        start, end = self._calc_prefetch_pieces(ti, idx)
        
        piece_prios = h.get_piece_priorities()
        for p in range(start, min(end + 1, len(piece_prios))):
            piece_prios[p] = 4
        h.prioritize_pieces(piece_prios)
        
        print(f"[torrent] prefetch: {hash_str} pieces {start}-{end}")
        return True
    
    def get_status(self, hash_str):
        """获取 torrent 状态"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info:
            return None
        
        h = info["handle"]
        s = h.status()
        
        # 计算实际磁盘使用量
        disk_usage = 0
        if info["video_path"] and os.path.exists(info["video_path"]):
            disk_usage = os.path.getsize(info["video_path"])
        
        return {
            "hash": hash_str,
            "name": s.name,
            "ready": info["ready"] and s.has_metadata,
            "peers": s.num_peers,
            "progress": s.progress * 100,
            "download_rate": s.download_rate,
            "upload_rate": s.upload_rate,
            "video_file": os.path.basename(info["video_path"]) if info["video_path"] else None,
            "video_size": info["video_size"],
            "disk_usage": disk_usage,
            "state": str(s.state),
        }
    
    def get_all_status(self):
        """获取所有 torrent 状态"""
        with self.lock:
            hashes = list(self.torrents.keys())
        return [self.get_status(h) for h in hashes]
    
    def is_ready_to_play(self, hash_str):
        """检查是否已下载足够数据可以开始播放"""
        with self.lock:
            info = self.torrents.get(hash_str)
        if not info or not info["video_path"]:
            return False
        
        # 检查文件前 PLAY_READY_BYTES 是否已有数据（非洞）
        path = info["video_path"]
        if not os.path.exists(path):
            return False
        
        try:
            with open(path, "rb") as f:
                # 检查 offset 0 和 offset video_size-1MB 是否有数据
                f.seek(0)
                head = f.read(1024)
                if all(b == 0 for b in head):
                    return False
                
                # 检查尾部（moov 可能在尾部）
                tail_offset = max(0, info["video_size"] - 1024 * 1024)
                f.seek(tail_offset)
                tail = f.read(1024)
                if all(b == 0 for b in tail):
                    return False
                
                return True
        except Exception:
            return False
    
    def evict_if_needed(self):
        """LRU 淘汰：删除最旧的已完成 torrent"""
        total = self._get_cache_size()
        if total < self.max_size_bytes:
            return
        
        with self.lock:
            # 按 last_access 排序，删除最旧的
            sorted_items = sorted(
                self.torrents.items(),
                key=lambda x: x[1]["last_access"]
            )
        
        for hash_str, info in sorted_items:
            if total < self.max_size_bytes * 0.8:
                break
            
            print(f"[lru] evicting {hash_str}")
            self.session.remove_torrent(info["handle"])
            
            # 删除文件（punch hole）
            save_path = info["handle"].status().save_path
            try:
                import shutil
                shutil.rmtree(save_path, ignore_errors=True)
            except Exception as e:
                print(f"[lru] rm error: {e}")
            
            with self.lock:
                if hash_str in self.torrents:
                    del self.torrents[hash_str]
            
            total = self._get_cache_size()
    
    def _get_cache_size(self):
        """计算 torrent 缓存目录总大小"""
        total = 0
        if not os.path.exists(self.cache_dir):
            return 0
        for dirpath, dirnames, filenames in os.walk(self.cache_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    # 使用 os.stat 获取实际磁盘使用（考虑稀疏文件）
                    st = os.stat(fp)
                    total += st.st_blocks * 512  # 实际磁盘块大小
                except OSError:
                    pass
        return total
    
    def shutdown(self):
        """关闭引擎"""
        self._stop = True
        self._alert_thread.join(timeout=5)


# ── HTTP 处理器 ───────────────────────────────────────
class CacheHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, engine=None, **kwargs):
        self.engine = engine
        super().__init__(*args, **kwargs)
    
    def log_message(self, format, *args):
        msg = format % args
        if ".ts" in msg or ".m3u8" in msg or ".jpg" in msg or "stream" in msg:
            print(f"[serve] {msg.strip()}")
    
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
        if path == "/" or path == "/actresses-report.html":
            return os.path.join(WORKSPACE_DIR, "actresses-report.html")
        ws_path = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
        if os.path.exists(ws_path):
            return ws_path
        return super().translate_path(path)
    
    def _serve_video(self, hash_str):
        """直接从稀疏文件提供视频流"""
        info = self.engine.torrents.get(hash_str)
        if not info or not info["video_path"]:
            self.send_error(404, "Torrent not found")
            return
        
        path = info["video_path"]
        if not os.path.exists(path):
            self.send_error(404, "Video file not found")
            return
        
        # 获取文件大小（稀疏文件的逻辑大小）
        total_size = info["video_size"]
        if total_size == 0:
            self.send_error(500, "Video size unknown")
            return
        
        info["last_access"] = time.time()
        
        range_hdr = self.headers.get("Range")
        if range_hdr:
            parts = range_hdr.replace("bytes=", "").split("-")
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else total_size - 1
            chunk_size = (end - start) + 1
            
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
                    buf = f.read(min(65536, remaining))
                    if not buf:
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
                    buf = f.read(65536)
                    if not buf:
                        break
                    self.wfile.write(buf)
    
    def do_GET(self):
        path = unquote(self.path)
        
        # 视频流
        stream_match = re.match(r"^/torrent/stream/([a-f0-9]{40})$", path, re.I)
        if stream_match:
            self._serve_video(stream_match.group(1).lower())
            return
        
        # 状态查询
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
        
        # 缓存状态聚合
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
        
        # 根路径帮助
        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Torrent Cache Server\n")
            return
        
        # 静态文件（fallback）
        super().do_GET()
    
    def do_POST(self):
        path = unquote(self.path)
        
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
                
                # 播放模式：设置为全速下载
                if not prefetch:
                    self.engine.set_full_priority(hash_str)
                
                # 等待 metadata 就绪
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


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="一体化 BitTorrent 缓存流服务器")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    parser.add_argument("--max-size", type=int, default=20, help="最大缓存大小 GB (默认: 20)")
    args = parser.parse_args()
    
    engine = TorrentEngine(CACHE_DIR, args.max_size)
    
    # 注册信号处理
    def signal_handler(sig, frame):
        print("\n[shutdown] Stopping server...")
        engine.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    os.chdir(WORKSPACE_DIR)
    
    def handler_factory(*args, **kwargs):
        return CacheHandler(*args, engine=engine, **kwargs)
    
    server = ThreadedHTTPServer(("0.0.0.0", args.port), handler_factory)
    
    print("=" * 60)
    print(f"Torrent Cache Server started at http://localhost:{args.port}/")
    print(f"Cache directory: {CACHE_DIR}")
    print(f"Max cache size: {args.max_size}GB")
    print(f"Engine: libtorrent {lt.version}")
    print("=" * 60)
    print("Open http://localhost:{args.port}/ in your browser")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    
    server.serve_forever()


if __name__ == "__main__":
    main()
