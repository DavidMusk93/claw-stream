from __future__ import annotations

from .stream import stream_router, check_router
from .torrents import router as torrents_router
from .cache import router as cache_router
from .auth import router as auth_router
from .log import router as log_router

__all__ = ["stream_router", "check_router", "torrents_router", "cache_router", "auth_router", "log_router"]
