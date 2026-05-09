from __future__ import annotations

import asyncio
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Any

from backend.models import CacheMetrics
from backend.services.torrent_engine import format_size

router = APIRouter(prefix="/api/cache", tags=["cache"])


def get_engine(request: Request) -> Any:
    return request.app.state.engine


@router.get("")
async def get_cache(engine: Any = Depends(get_engine)):
    """Get all cache items and total size."""
    items = await asyncio.to_thread(engine.get_all_status)
    total_disk = await asyncio.to_thread(engine._get_cache_size)
    return {
        "totalSize": total_disk,
        "maxSize": engine.max_size_bytes,
        "itemCount": len(items),
        "items": items,
    }


@router.delete("/{hash_str}")
async def delete_cache(hash_str: str, engine: Any = Depends(get_engine)):
    """Remove a torrent from cache."""
    success = await asyncio.to_thread(engine.remove_torrent, hash_str)
    return {"deleted": success}


@router.get("/metrics", response_model=CacheMetrics)
async def get_metrics(engine: Any = Depends(get_engine)):
    """Get cache metrics summary."""
    items = await asyncio.to_thread(engine.get_all_status)
    total_disk = await asyncio.to_thread(engine._get_cache_size)
    completed = sum(1 for i in items if i.get("progress", 0) >= 99.9)
    return CacheMetrics(
        total=len(items),
        completed=completed,
        downloading=len(items) - completed,
        used_bytes=total_disk,
        used_human=format_size(total_disk),
        max_bytes=engine.max_size_bytes,
        max_human=format_size(engine.max_size_bytes),
    )
