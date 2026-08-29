from __future__ import annotations

from .stream import stream_router, check_router
from .torrents import router as torrents_router
from .cache import router as cache_router
from .auth import router as auth_router
from .log import router as log_router
from .sync import router as sync_router
from .track import router as track_router
from .test_helper import router as test_router
from .events import router as events_router

__all__ = [
    "stream_router",
    "check_router",
    "torrents_router",
    "cache_router",
    "auth_router",
    "log_router",
    "sync_router",
    "track_router",
    "test_router",
    "events_router",
]
