from __future__ import annotations

import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.routers import stream_router, check_router, torrents_router, cache_router, auth_router
from backend.services.torrent_engine import TorrentEngine
from logger import get_logger

log = get_logger("backend")

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache", "torrent")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")


def _get_engine() -> TorrentEngine:
    """Create TorrentEngine singleton."""
    return TorrentEngine(CACHE_DIR, max_size_gb=20)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    # Startup
    log.info("Backend starting up...")
    engine = _get_engine()
    app.state.engine = engine
    log.info(f"TorrentEngine initialized, cache dir: {CACHE_DIR}")

    yield

    # Shutdown
    log.info("Backend shutting down...")
    engine.shutdown()
    log.info("TorrentEngine stopped")


app = FastAPI(
    title="Actress Report Backend",
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

# API routes
app.include_router(stream_router)
app.include_router(check_router)
app.include_router(torrents_router)
app.include_router(cache_router)
app.include_router(auth_router)

# Static files
if os.path.exists(IMAGES_DIR):
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

if os.path.exists(CACHE_DIR):
    app.mount("/cache", StaticFiles(directory=CACHE_DIR), name="cache")


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
