from __future__ import annotations

import asyncio
from fastapi import APIRouter, Request, Depends, HTTPException
from typing import Any

import os

from backend.models import CacheMetrics
from backend.services.torrent_engine import format_size
from core.events import publish_event

router = APIRouter(prefix="/api/cache", tags=["cache"])


def get_engine(request: Request) -> Any:
    return request.app.state.engine


@router.get("")
async def get_cache(engine: Any = Depends(get_engine)):
    """Get all cache items with actual data (local_size > 0)."""
    all_items = await asyncio.to_thread(engine.get_all_status)
    items = [i for i in all_items if i.get("local_size", 0) > 0]
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
    await publish_event("cache.update", {"action": "delete", "hash": hash_str})
    return {"deleted": success}


@router.post("/gc-orphans")
async def gc_orphans(engine: Any = Depends(get_engine)):
    """Manually trigger orphan torrent GC: clean up torrents that exist on disk but have no corresponding record in the database."""
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db_path = os.path.join(script_dir, "data", "claw.duckdb")
    removed = await asyncio.to_thread(engine.gc_orphaned_torrents, db_path)
    await publish_event("cache.update", {"action": "gc", "removed": removed})
    return {"removed": removed}


@router.get("/metrics", response_model=CacheMetrics)
async def get_metrics(engine: Any = Depends(get_engine)):
    """Get cache metrics summary (only items with actual data)."""
    all_items = await asyncio.to_thread(engine.get_all_status)
    items = [i for i in all_items if i.get("local_size", 0) > 0]
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
