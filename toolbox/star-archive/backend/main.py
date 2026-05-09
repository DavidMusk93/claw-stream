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

from backend.routers import stream_router, check_router, torrents_router, cache_router, auth_router, log_router, stars
from backend.services.torrent_engine import TorrentEngine
from core import get_logger, set_trace_id

log = get_logger("backend")
access_log = get_logger("backend-access")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")


def _get_engine() -> TorrentEngine:
    """Create TorrentEngine singleton."""
    return TorrentEngine(CACHE_DIR, max_size_gb=20)


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

# Static files
if os.path.exists(IMAGES_DIR):
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

if os.path.exists(CACHE_DIR):
    app.mount("/cache", StaticFiles(directory=CACHE_DIR), name="cache")


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")

# 持久化只读 DuckDB 连接（封面查询复用）
_cover_db: duckdb.DuckDBPyConnection | None = None


def _get_cover_db() -> duckdb.DuckDBPyConnection:
    global _cover_db
    if _cover_db is None:
        _cover_db = duckdb.connect(DB_PATH, read_only=True)
    return _cover_db


@app.get("/api/cover/{code}")
async def cover_image(code: str):
    """从 DuckDB cover_b64 字段读取封面并返回二进制图片"""
    conn = _get_cover_db()
    row = conn.execute(
        "SELECT cover_b64 FROM titles WHERE code = ? AND cover_b64 IS NOT NULL AND cover_b64 != '' LIMIT 1",
        (code.upper(),),
    ).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "Cover not found"})

    b64_data = row[0]
    # 去掉 data:image/jpeg;base64, 前缀（如果存在）
    if b64_data.startswith("data:image/"):
        b64_data = b64_data.split(",", 1)[1]

    try:
        image_bytes = base64.b64decode(b64_data)
    except Exception:
        return JSONResponse(status_code=500, content={"detail": "Invalid base64"})

    return Response(
        content=image_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
