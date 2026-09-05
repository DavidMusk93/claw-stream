from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import time
import uuid
import base64

import duckdb

from backend.routers import stream_router, check_router, torrents_router, cache_router, auth_router, log_router, sync_router, track_router, stars, test_router, events_router
from backend.services.torrent_engine import TorrentEngine
from core import get_logger, set_trace_id

log = get_logger("backend")
access_log = get_logger("backend-access")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")


def _guess_image_mime(data: bytes) -> str:
    """Guess image MIME type from binary header."""
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
    return "image/jpeg"  # Default fallback


def _get_engine() -> TorrentEngine:
    """Create TorrentEngine singleton."""
    return TorrentEngine(CACHE_DIR, max_size_gb=0)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request (method, URL, status code, elapsed time, client IP, trace_id)."""
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

    # Ensure DuckDB schema is initialized (idempotent)
    from core import db as _db
    _db.init_schema()
    log.info("DuckDB schema initialized")

    # Load user-liked titles into the engine protection set
    try:
        conn = duckdb.connect(os.path.join(SCRIPT_DIR, "data", "claw.duckdb"))
        try:
            rows = conn.execute(
                "SELECT magnet_hash FROM titles WHERE user_liked = 1 AND magnet_hash IS NOT NULL"
            ).fetchall()
            for (h,) in rows:
                if h:
                    engine.set_liked(h, True)
            log.info(f"Loaded {len(rows)} liked torrents into cache protection set")
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"Failed to load liked torrents: {e}")

    # After preload finishes, resume all liked torrents so they start
    # downloading instead of staying paused until the user clicks play.
    async def _resume_liked_after_preload() -> None:
        try:
            await asyncio.to_thread(engine._preload_thread.join)
            await asyncio.sleep(1)
            await asyncio.to_thread(engine.resume_liked_torrents)
        except Exception as e:
            log.warning(f"Failed to resume liked torrents: {e}")

    asyncio.create_task(_resume_liked_after_preload())

    # Start the 6h scheduled title sync (records runs in sync_runs table)
    from backend.routers.sync import start_sync_scheduler
    start_sync_scheduler()

    # Clean up orphan torrents on startup (caused by historical bugs or interrupted deletion flows)
    try:
        db_path = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")
        removed = await asyncio.to_thread(engine.gc_orphaned_torrents, db_path)
        log.info(f"Startup GC removed {removed} orphan torrent(s)")
    except Exception as e:
        log.warning(f"Startup GC failed: {e}")

    yield

    # Shutdown
    log.info("Backend shutting down...")
    engine.shutdown()
    log.info("TorrentEngine stopped")


async def _global_exception_handler(request: Request, exc: Exception):
    """Global exception handler: log unhandled exceptions and return a unified error response."""
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

# CORS: default to same-origin dev origins; production should set CORS_ORIGINS env var.
# Using "*" with credentials is unsafe and is intentionally not supported.
_cors_raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()] or ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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
app.include_router(track_router)
app.include_router(test_router)
app.include_router(events_router)

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

# In-memory LRU cache: avoid repeatedly reading the same cover from disk.
# Covers are immutable, so a modest cache dramatically reduces disk I/O.
_cover_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_COVER_CACHE_MAX = 1000


def _cover_disk_path(code_upper: str, thumb: bool = False) -> str:
    """Return the canonical on-disk JPEG path for a title code."""
    code_lower = code_upper.lower()
    suffix = "_thumb" if thumb else ""
    return os.path.join(IMAGES_DIR, "titles", code_lower, f"{code_lower}{suffix}.jpg")


def _read_cover_from_disk(code_upper: str) -> tuple[bytes, str] | None:
    """Read cover from the canonical disk path."""
    file_path = _cover_disk_path(code_upper)
    try:
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                return f.read(), "image/jpeg"
    except Exception:
        pass
    return None


def _write_cover_to_disk(code_upper: str, image_bytes: bytes) -> None:
    """Persist decoded cover bytes to the canonical disk path."""
    file_path = _cover_disk_path(code_upper)
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(image_bytes)
    except Exception:
        pass


def _read_cover_from_db(code_upper: str) -> tuple[bytes, str] | None:
    """Synchronous function: read cover from disk first, then DuckDB, and backfill disk."""
    # 1. Prefer disk: covers exported by scripts/export_covers.py live here.
    disk_result = _read_cover_from_disk(code_upper)
    if disk_result:
        return disk_result

    # 2. Fallback to DuckDB base64 blob.
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
                    # Backfill disk so subsequent requests avoid DuckDB entirely.
                    _write_cover_to_disk(code_upper, image_bytes)
                    return image_bytes, media_type
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        pass

    return None


def _get_cached_cover(code_upper: str) -> tuple[bytes, str] | None:
    """LRU cache lookup: move hit to the end (most recently used)."""
    if code_upper in _cover_cache:
        value = _cover_cache.pop(code_upper)
        _cover_cache[code_upper] = value
        return value
    return None


def _set_cached_cover(code_upper: str, value: tuple[bytes, str]) -> None:
    """LRU cache insert: evict oldest entry when over capacity."""
    if code_upper in _cover_cache:
        _cover_cache.move_to_end(code_upper)
        return
    if len(_cover_cache) >= _COVER_CACHE_MAX:
        _cover_cache.popitem(last=False)
    _cover_cache[code_upper] = value


@app.get("/api/cover/{code}")
async def cover_image(code: str, thumb: int = 0):
    """Canonical cover endpoint: redirect to static file when cached, else serve from DB.

    This is the single source of truth for cover URLs. If the normalized JPEG
    already exists on disk, redirect to `/images/titles/{code}/{code}.jpg` so
    Caddy can serve it directly. Otherwise fall back to DuckDB, backfill disk,
    and stream the bytes. New titles therefore never 404 even if the disk sync
    lagged or failed.

    With ``?thumb=1`` the small ``{code}_thumb.jpg`` variant (generated by
    scripts/export_covers.py) is preferred; missing thumbs fall through to the
    full-size pipeline.
    """
    code_upper = code.upper()
    code_lower = code_upper.lower()

    # 0. Thumbnail requested and on disk: redirect to the small variant.
    if thumb:
        thumb_file = _cover_disk_path(code_upper, thumb=True)
        if os.path.exists(thumb_file):
            return RedirectResponse(
                url=f"/images/titles/{code_lower}/{code_lower}_thumb.jpg",
                status_code=307,
                headers={"Cache-Control": "public, max-age=604800, immutable"},
            )

    # 1. Static file cache hit: let Caddy serve directly on the next request.
    static_path = f"/images/titles/{code_lower}/{code_lower}.jpg"
    disk_file = _cover_disk_path(code_upper)
    if os.path.exists(disk_file):
        return RedirectResponse(
            url=static_path,
            status_code=307,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    # 2. In-memory LRU cache hit.
    cached = _get_cached_cover(code_upper)
    if cached:
        image_bytes, media_type = cached
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    # 3. Fall back to DB and backfill disk for future requests.
    result = await asyncio.to_thread(_read_cover_from_db, code_upper)
    if result:
        _set_cached_cover(code_upper, result)
        image_bytes, media_type = result
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    return JSONResponse(status_code=404, content={"detail": "Cover not found"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
