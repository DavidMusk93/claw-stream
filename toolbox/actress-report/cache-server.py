#!/usr/bin/env python3
"""
cache-server.py — 本地视频缓存文件管理服务器

功能：
1. 扫描 cache/video/<hash>/ 目录，发现本地 MP4 缓存文件
2. 提供 HTTP 流式播放（Range 请求，直接从本地文件读取）
3. 精细缓存管理：显示每个文件大小、删除按钮
4. 静态文件服务（HTML、图片）

用法：
  cd toolbox/actress-report && python3 cache-server.py

然后浏览器访问：http://localhost:8765/
"""

import os, sys, json, re, shutil, time
from urllib.parse import unquote
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "video")
REPORT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(os.path.dirname(REPORT_DIR))

os.makedirs(CACHE_DIR, exist_ok=True)


def find_video_file(hash_str):
    """查找某个 hash 对应的本地视频文件"""
    dir_path = os.path.join(CACHE_DIR, hash_str)
    if not os.path.exists(dir_path):
        return None
    
    # 查找最大的视频文件
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm"}
    best = None
    best_size = 0
    
    for f in os.listdir(dir_path):
        ext = os.path.splitext(f)[1].lower()
        if ext not in video_exts:
            continue
        fp = os.path.join(dir_path, f)
        try:
            size = os.path.getsize(fp)
            if size > best_size:
                best_size = size
                best = fp
        except OSError:
            pass
    
    return best


def get_all_cache_items():
    """获取所有缓存文件信息"""
    items = []
    if not os.path.exists(CACHE_DIR):
        return items
    
    for hash_str in os.listdir(CACHE_DIR):
        dir_path = os.path.join(CACHE_DIR, hash_str)
        if not os.path.isdir(dir_path):
            continue
        
        video_path = find_video_file(hash_str)
        if not video_path:
            continue
        
        stat = os.stat(video_path)
        items.append({
            "hash": hash_str,
            "name": os.path.basename(video_path),
            "size": stat.st_size,
            "disk_usage": stat.st_blocks * 512,  # 实际磁盘使用（稀疏文件）
            "mtime": stat.st_mtime,
            "path": video_path,
        })
    
    # 按修改时间降序
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def get_cache_size():
    """计算缓存目录实际磁盘使用（考虑稀疏文件）"""
    total = 0
    if not os.path.exists(CACHE_DIR):
        return 0
    for dirpath, dirnames, filenames in os.walk(CACHE_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                st = os.stat(fp)
                total += st.st_blocks * 512
            except OSError:
                pass
    return total


class CacheHandler(SimpleHTTPRequestHandler):
    """自定义 HTTP 请求处理器"""
    
    def log_message(self, format, *args):
        msg = format % args
        if ".ts" in msg or ".m3u8" in msg or ".jpg" in msg or "stream" in msg:
            print(f"[serve] {msg.strip()}")
    
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
        if path == "/" or path == "/actresses-report.html":
            return os.path.join(WORKSPACE_DIR, "actresses-report.html")
        ws_path = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
        if os.path.exists(ws_path):
            return ws_path
        return super().translate_path(path)
    
    def _serve_video(self, hash_str):
        """直接从本地文件提供视频流"""
        video_path = find_video_file(hash_str)
        if not video_path:
            self.send_error(404, "Video not cached. Download the magnet and place the file in cache/video/<hash>/")
            return
        
        total_size = os.path.getsize(video_path)
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
            
            with open(video_path, "rb") as f:
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
            
            with open(video_path, "rb") as f:
                while True:
                    buf = f.read(65536)
                    if not buf:
                        break
                    self.wfile.write(buf)
    
    def do_GET(self):
        path = unquote(self.path)
        
        # 视频流
        stream_match = re.match(r"^/stream/([a-f0-9]{40})$", path, re.I)
        if stream_match:
            self._serve_video(stream_match.group(1).lower())
            return
        
        # 缓存状态 API
        if path == "/api/cache":
            items = get_all_cache_items()
            total_disk = get_cache_size()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "totalSize": total_disk,
                "itemCount": len(items),
                "items": items,
            }).encode())
            return
        
        # 检查某个 hash 是否有缓存
        check_match = re.match(r"^/api/check/([a-f0-9]{40})$", path, re.I)
        if check_match:
            hash_str = check_match.group(1).lower()
            video_path = find_video_file(hash_str)
            exists = video_path is not None and os.path.getsize(video_path) > 1024 * 1024
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "hash": hash_str,
                "cached": exists,
                "path": video_path,
                "size": os.path.getsize(video_path) if video_path else 0,
            }).encode())
            return
        
        # 根路径返回 HTML 报告
        if path == "/":
            report_path = os.path.join(WORKSPACE_DIR, "actresses-report.html")
            if os.path.exists(report_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(report_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"actresses-report.html not found")
            return
        
        super().do_GET()
    
    def do_DELETE(self):
        path = unquote(self.path)
        
        # 删除某个缓存
        cache_match = re.match(r"^/api/cache/([a-f0-9]{40})$", path, re.I)
        if cache_match:
            hash_str = cache_match.group(1).lower()
            dir_path = os.path.join(CACHE_DIR, hash_str)
            if os.path.exists(dir_path):
                try:
                    shutil.rmtree(dir_path)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"deleted": True}).encode())
                    return
                except Exception as e:
                    self.send_error(500, str(e))
                    return
            else:
                self.send_error(404, "Not found")
                return
        
        self.send_error(405, "Method not allowed")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="本地视频缓存文件管理服务器")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    args = parser.parse_args()
    
    os.chdir(WORKSPACE_DIR)
    server = ThreadedHTTPServer(("0.0.0.0", args.port), CacheHandler)
    
    items = get_all_cache_items()
    total = get_cache_size()
    
    print("=" * 60)
    print(f"Cache Server started at http://localhost:{args.port}/")
    print(f"Cache directory: {CACHE_DIR}")
    print(f"Cached videos: {len(items)}")
    print(f"Total disk usage: {total / 1024 / 1024:.1f}MB")
    print("=" * 60)
    print(f"Open http://localhost:{args.port}/ in your browser")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
