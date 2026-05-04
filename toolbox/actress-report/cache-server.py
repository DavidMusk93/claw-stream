#!/usr/bin/env python3
"""cache-server.py — 本地视频缓存代理服务器

功能：
1. 提供静态文件服务（HTML、图片、缓存的视频片段）
2. 运行时动态下载未缓存的视频片段并保存到本地
3. LRU 缓存淘汰（默认最大 20GB）
4. 解决 file:// 协议的 CORS 限制

用法：
  cd toolbox/actress-report && python3 cache-server.py

然后浏览器访问：http://localhost:8765/
"""

import os, sys, json, re, time, threading
from urllib.parse import urlparse, urljoin, unquote
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

# 尝试导入 httpx，如果没有则用 urllib
HTTPX_AVAILABLE = False
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
JABLE_DIR = "/tmp/actress-jable"
REPORT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(os.path.dirname(REPORT_DIR))

MAX_CACHE_SIZE_GB = 20
MAX_CACHE_SIZE_BYTES = MAX_CACHE_SIZE_GB * 1024 * 1024 * 1024

cache_lock = threading.Lock()


def get_cache_size():
    """计算缓存目录总大小"""
    total = 0
    if not os.path.exists(CACHE_DIR):
        return 0
    for dirpath, dirnames, filenames in os.walk(CACHE_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def lru_evict(target_free_bytes):
    """LRU 淘汰：删除最旧的文件直到腾出足够空间"""
    files = []
    for dirpath, dirnames, filenames in os.walk(CACHE_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                files.append((os.path.getatime(fp), os.path.getsize(fp), fp))
            except OSError:
                pass
    files.sort()  # 按访问时间排序，最旧的在前

    freed = 0
    for atime, size, fp in files:
        if freed >= target_free_bytes:
            break
        try:
            os.remove(fp)
            freed += size
        except OSError:
            pass
    return freed


def find_original_m3u8_url(code):
    """从 jable JSON 中找到原始 m3u8 URL"""
    json_path = os.path.join(JABLE_DIR, f"{code.upper()}.json")
    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                data = json.load(f)
            for w in data.get("works", []):
                if w.get("m3u8_url"):
                    return w["m3u8_url"]
        except Exception:
            pass
    return None


def fetch_segment(original_url, local_path):
    """下载片段到本地缓存"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "Referer": "https://en.jable.tv/",
    }
    try:
        if HTTPX_AVAILABLE:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(original_url, headers=headers)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    return True
        else:
            req = urllib.request.Request(original_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if len(data) > 1000:
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(data)
                    return True
    except Exception as e:
        print(f"[proxy] download error: {e}")
    return False


class CacheHandler(SimpleHTTPRequestHandler):
    """自定义 HTTP 请求处理器"""

    def log_message(self, format, *args):
        msg = format % args
        if ".ts" in msg or ".m3u8" in msg or ".jpg" in msg:
            print(f"[serve] {msg.strip()}")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Cache-Control", "public, max-age=31536000")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def translate_path(self, path):
        """将 URL 路径映射到本地文件路径"""
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

    def _proxy_to_torrent(self, method, target_path):
        """将请求转发到 torrent-server (localhost:8768)，流式传输避免内存问题"""
        import urllib.request

        try:
            body = None
            if method == "POST":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length > 0:
                    body = self.rfile.read(content_length)

            # 收集需要转发的请求头
            headers = {}
            for key in ("Range", "Accept", "User-Agent", "Content-Type"):
                value = self.headers.get(key)
                if value:
                    headers[key] = value

            req = urllib.request.Request(
                f"http://localhost:8768{target_path}",
                data=body,
                method=method,
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "content-encoding"):
                        # 避免重复 CORS 头（torrent-server 已设置）
                        if key.lower() in ("access-control-allow-origin", "access-control-allow-methods", "access-control-allow-headers"):
                            continue
                        self.send_header(key, value)
                # 流式传输：分 64KB 块读写
                import shutil
                self.end_headers()
                shutil.copyfileobj(resp, self.wfile)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for key, value in e.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            print(f"[proxy] torrent error: {e}")
            self.send_error(502, f"Torrent server error: {e}")

    def do_GET(self):
        path = unquote(self.path)

        # 缓存状态 API
        if path == "/api/cache":
            self._proxy_to_torrent("GET", "/cache")
            return

        # 反向代理 torrent-server 请求
        if path.startswith("/torrent"):
            self._proxy_to_torrent("GET", path[len("/torrent"):])
            return

        # 处理视频片段代理请求
        if path.startswith("/cache/") and (path.endswith(".ts") or path.endswith(".key")):
            local_path = os.path.join(CACHE_DIR, path[7:])

            if os.path.exists(local_path) and os.path.getsize(local_path) > 100:
                return super().do_GET()

            with cache_lock:
                if os.path.exists(local_path) and os.path.getsize(local_path) > 100:
                    return super().do_GET()

                current_size = get_cache_size()
                if current_size >= MAX_CACHE_SIZE_BYTES * 0.95:
                    target_free = int(MAX_CACHE_SIZE_BYTES * 0.2)
                    freed = lru_evict(target_free)
                    print(f"[cache] LRU evicted {freed / 1024 / 1024:.1f}MB")

                parts = path[7:].split("/")
                if len(parts) >= 2:
                    code = parts[0].upper()
                    seg_name = parts[-1]

                    m3u8_url = find_original_m3u8_url(code)
                    if m3u8_url:
                        base_url = m3u8_url.rsplit("/", 1)[0] + "/"
                        original_url = urljoin(base_url, seg_name)
                        print(f"[proxy] downloading {code}/{seg_name}...")

                        if fetch_segment(original_url, local_path):
                            print(f"[proxy] cached {code}/{seg_name}")
                            return super().do_GET()
                        else:
                            print(f"[proxy] failed {code}/{seg_name}")

            self.send_error(404, "Segment not cached")
            return

        if path.startswith("/cache/") and path.endswith(".m3u8"):
            local_path = os.path.join(CACHE_DIR, path[7:])
            if os.path.exists(local_path):
                return super().do_GET()
            self.send_error(404, "M3U8 not found")
            return

        return super().do_GET()

    def do_POST(self):
        path = unquote(self.path)
        if path.startswith("/torrent"):
            self._proxy_to_torrent("POST", path[len("/torrent"):])
            return
        self.send_error(405, "Method not allowed")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="本地视频缓存代理服务器")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    parser.add_argument("--max-size", type=int, default=20, help="最大缓存大小 GB (默认: 20)")
    args = parser.parse_args()

    global MAX_CACHE_SIZE_BYTES
    MAX_CACHE_SIZE_BYTES = args.max_size * 1024 * 1024 * 1024

    os.chdir(WORKSPACE_DIR)

    server = ThreadedHTTPServer(("0.0.0.0", args.port), CacheHandler)
    print(f"=" * 60)
    print(f"Cache Server started at http://localhost:{args.port}/")
    print(f"Cache directory: {CACHE_DIR}")
    print(f"Max cache size: {args.max_size}GB")
    print(f"Current cache size: {get_cache_size() / 1024 / 1024:.1f}MB")
    print(f"=" * 60)
    print(f"Open http://localhost:{args.port}/ in your browser")
    print(f"Press Ctrl+C to stop")
    print(f"=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
