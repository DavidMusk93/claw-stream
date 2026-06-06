from __future__ import annotations

import asyncio
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import time
import uuid
import base64

import duckdb

from backend.routers import stream_router, check_router, torrents_router, cache_router, auth_router, log_router, sync_router, stars, test_router
from backend.services.torrent_engine import TorrentEngine
from core import get_logger, set_trace_id

log = get_logger("backend")
access_log = get_logger("backend-access")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")


def _guess_image_mime(data: bytes) -> str:
    """根据二进制头部推断图片 MIME 类型。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"  # 默认回退


def _get_engine() -> TorrentEngine:
    """Create TorrentEngine singleton."""
    return TorrentEngine(CACHE_DIR, max_size_gb=0)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """记录每个 HTTP 请求的访问日志（方法、URL、状态码、耗时、客户端 IP、trace_id）"""
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        client = request.client.host if request.client else "-"
        method = request.method
        path = request.url.path
        tid = request.headers.get("x-trace-id") or uuid.uuid4().hex[:16]
        set_trace_id(tid)

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            access_log.error(
                f"{method} {path} {client} -> {type(exc).__name__}: {exc} ({elapsed:.1f}ms)",
                exc_info=True,
            )
            raise

        elapsed = (time.perf_counter() - start) * 1000
        access_log.info(f"{method} {path} {client} -> {status_code} ({elapsed:.1f}ms)")
        response.headers["x-trace-id"] = tid
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    # Startup
    log.info("Backend starting up...")

    # Scale the default thread pool — most of our work is blocking file I/O
    # (read_video_range, find_video_state, DuckDB queries, libtorrent calls).
    # Default min(32, cpu+4) = 7 on a 3-core box is far too small under load.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=32, thread_name_prefix="star-io"))
    log.info("Thread pool scaled to 32 workers")

    engine = _get_engine()
    app.state.engine = engine
    log.info(f"TorrentEngine initialized, cache dir: {CACHE_DIR}")

    # 确保 DuckDB schema 已初始化（幂等）
    from core import db as _db
    _db.init_schema()
    log.info("DuckDB schema initialized")

    yield

    # Shutdown
    log.info("Backend shutting down...")
    engine.shutdown()
    log.info("TorrentEngine stopped")


async def _global_exception_handler(request: Request, exc: Exception):
    """全局异常捕获：记录未处理的异常并返回统一错误响应"""
    access_log.error(
        f"Unhandled exception on {request.method} {request.url.path}: {type(exc).__name__}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


app = FastAPI(
    title="Star Archive Backend",
    description="BitTorrent cache + video streaming API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Access logging middleware
app.add_middleware(AccessLogMiddleware)

# Global exception handler
app.add_exception_handler(Exception, _global_exception_handler)

# API routes
app.include_router(stream_router)
app.include_router(check_router)
app.include_router(torrents_router)
app.include_router(cache_router)
app.include_router(auth_router)
app.include_router(log_router)
app.include_router(stars.router)
app.include_router(sync_router)
app.include_router(test_router)

# Static files
if os.path.exists(IMAGES_DIR):
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# NOTE: /cache is intentionally NOT mounted as StaticFiles to prevent
# direct download of video files. Use /api/cache/* and /stream/* instead.


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")

# 内存缓存：避免重复查询 DuckDB（封面不会变化）
_cover_cache: dict[str, tuple[bytes, str]] = {}


def _read_cover_from_db(code_upper: str) -> tuple[bytes, str] | None:
    """同步函数：从 DuckDB 或文件系统读取封面，返回 (image_bytes, media_type)。"""
    # 1. 优先从 DuckDB 读取
    try:
        conn = duckdb.connect(DB_PATH)
        try:
            row = conn.execute(
                "SELECT cover_b64 FROM titles WHERE code = ? AND cover_b64 IS NOT NULL AND cover_b64 != '' LIMIT 1",
                (code_upper,),
            ).fetchone()
            if row and row[0]:
                b64_data = row[0]
                if b64_data.startswith("data:image/"):
                    b64_data = b64_data.split(",", 1)[1]
                try:
                    image_bytes = base64.b64decode(b64_data)
                    media_type = _guess_image_mime(image_bytes)
                    return image_bytes, media_type
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        pass

    # 2. 回退到文件系统 images/titles/{code}/{code}.jpg
    code_lower = code_upper.lower()
    cover_dir = os.path.join(IMAGES_DIR, "titles", code_lower)
    if os.path.isdir(cover_dir):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            file_path = os.path.join(cover_dir, f"{code_lower}{ext}")
            if os.path.exists(file_path):
                media_type = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                }.get(ext, "image/jpeg")
                with open(file_path, "rb") as f:
                    return f.read(), media_type
    return None


@app.get("/api/cover/{code}")
async def cover_image(code: str):
    """从 DuckDB cover_b64 字段读取封面，或回退到 images/titles/{code}/ 文件系统。
    
    使用线程池执行同步 I/O，避免阻塞事件循环。并发大量封面请求时，
    不会串行阻塞其他 API。
    """
    code_upper = code.upper()

    # 内存缓存命中直接返回（无需线程切换）
    cached = _cover_cache.get(code_upper)
    if cached:
        image_bytes, media_type = cached
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=604800"},
        )

    result = await asyncio.to_thread(_read_cover_from_db, code_upper)
    if result:
        image_bytes, media_type = result
        # 限制缓存大小，防止内存无限增长（简单 LRU：超过 1000 时清空）
        if len(_cover_cache) > 1000:
            _cover_cache.clear()
        _cover_cache[code_upper] = result
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=604800"},
        )

    return JSONResponse(status_code=404, content={"detail": "Cover not found"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
