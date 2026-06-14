"""backend/routers/stars.py — Actor aggregate data router

After the wide-table simplification, queries no longer JOIN magnets; read directly from titles.magnet.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException, Request, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Any

import duckdb

from backend.routers.auth import require_auth
from core import get_logger
from core.events import publish_event

log = get_logger("stars-router")
router = APIRouter(prefix="/api/stars", tags=["stars"], dependencies=[Depends(require_auth)])

CODE_PATTERN = r"^[A-Za-z0-9_-]+$"

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


# ── Config helpers ──────────────────────────────────────────────────

def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(config: dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


# ── Cache ───────────────────────────────────────────────────────────

_stars_cache: dict[str, Any] = {"data": None, "ts": 0}
CACHE_TTL = 5


def invalidate_stars_cache() -> None:
    global _stars_cache
    _stars_cache = {"data": None, "ts": 0}


# ── Response builder ────────────────────────────────────────────────

def _build_stars_response() -> list[dict[str, Any]]:
    config = _load_config()
    solo = [a for a in config.get("stars", []) if not a.get("type") or a.get("type") == "solo"]

    conn = duckdb.connect(DB_PATH)
    try:
        title_rows = conn.execute("""
        WITH ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY star_id
                    ORDER BY release_date_sort DESC NULLS LAST
                ) AS rn
            FROM titles
        )
        SELECT
            s.code,
            COALESCE(array_agg(struct_pack(
                code := r.code,
                title := r.title,
                date := IFNULL(r.release_date, ''),
                views := IFNULL(CAST(r.views AS VARCHAR), ''),
                likes := IFNULL(CAST(r.likes AS VARCHAR), ''),
                resolution := IFNULL(r.resolution, ''),
                download_url := IFNULL(r.download_url, ''),
                cover_url := IFNULL(r.cover_url, ''),
                charming_intro := IFNULL(r.charming_intro, ''),
                magnet := IFNULL(r.magnet, ''),
                user_liked := COALESCE(r.user_liked, 0)
            ) ORDER BY r.rn) FILTER (WHERE r.code IS NOT NULL), []) AS titles
        FROM stars s
        LEFT JOIN ranked r ON r.star_id = s.id AND r.rn <= 5
        GROUP BY s.id, s.code, s.name
        ORDER BY s.name
        """).fetchall()

        db_data: dict[str, dict[str, Any]] = {}
        for row in title_rows:
            db_data[row[0]] = {"titles": row[1]}

        result = []
        for a in solo:
            code = a["code"]
            data = db_data.get(code, {"titles": []})

            titles = data.get("titles", [])
            for t in titles:
                t["cover_url"] = f"/api/cover/{t['code']}"
                t["user_liked"] = bool(t.get("user_liked", 0))

            result.append({
                "name": a["name"],
                "jp": a.get("jp", ""),
                "handle": a.get("handle", ""),
                "code": code,
                "type": a.get("type", "solo"),
                "note": a.get("note", ""),
                "titles": titles,
            })

        def _latest_date(star):
            titles = star.get("titles", [])
            if titles and titles[0].get("date"):
                d = titles[0]["date"]
                if d and "/" in d:
                    parts = d.split("/")
                    return f"{parts[2]}{parts[1].zfill(2)}{parts[0].zfill(2)}"
            return "99999999"

        result.sort(key=_latest_date, reverse=True)

        number = 1
        for star in result:
            star["number"] = None
            is_first = True
            for t in star.get("titles", []):
                t["number"] = number
                t["is_primary"] = is_first
                if star["number"] is None:
                    star["number"] = number
                number += 1
                is_first = False

        return result
    finally:
        conn.close()


# ── Routes ──────────────────────────────────────────────────────────

@router.get("")
async def get_stars():
    """Get all stars with their latest titles and posts (cached, TTL=5s)."""
    global _stars_cache
    now = time.time()
    if _stars_cache["data"] is not None and (now - _stars_cache["ts"]) < CACHE_TTL:
        return JSONResponse(
            content=_stars_cache["data"],
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    data = _build_stars_response()
    _stars_cache = {"data": data, "ts": now}
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ── Add Star ────────────────────────────────────────────────────────

class AddStarRequest(BaseModel):
    star_page_url: str = Field(..., min_length=10)


class AddStarResponse(BaseModel):
    name: str
    code: str
    handle: str
    star_page_url: str
    titles_found: int


@router.post("/add")
async def add_star(request: AddStarRequest) -> AddStarResponse:
    """Add a new actor: parse homepage URL, dedupe, write to config.json and DB, background sync titles."""
    url = request.star_page_url.strip()

    # URL format validation
    if not url.startswith("https://ijavtorrent.com/actress/"):
        raise HTTPException(status_code=400, detail="URL 必须是 ijavtorrent actress 页面")

    m = re.search(r"/actress/([a-z0-9-]+)-(\d+)$", url)
    if not m:
        raise HTTPException(status_code=400, detail="URL 格式无法解析")
    slug, star_id = m.groups()
    handle = slug.replace("-", "_")

    # Load config and deduplicate
    config = _load_config()
    stars = config.get("stars", [])
    for s in stars:
        if s.get("star_page_url") == url:
            raise HTTPException(status_code=409, detail="Actor already exists")
        if s.get("handle") == handle:
            raise HTTPException(status_code=409, detail="该 handle 已被占用")

    # Fetch page and extract info
    from scrapers.v2.fetchers import HttpxFetcher
    from scrapers.v2.extractors import IJavTorrentExtractor
    from selectolax.parser import HTMLParser

    async with HttpxFetcher() as fetcher:
        html = await fetcher.fetch(url)

    tree = HTMLParser(html)
    h1 = tree.css_first("h1")
    name = h1.text().strip() if h1 else slug.replace("-", " ").title()

    # Extract titles from page, use the first title's code as the star code
    extractor = IJavTorrentExtractor()
    items = extractor.extract(html)
    if items:
        code = items[0].code
    else:
        code = f"PENDING-{star_id}"

    # Re-check code conflict
    for s in stars:
        if s.get("code") == code:
            raise HTTPException(status_code=409, detail=f"code {code} already used by another actor")

    new_star = {
        "name": name,
        "jp": name,
        "handle": handle,
        "code": code,
        "type": "solo",
        "star_page_url": url,
    }

    # Write to config.json
    stars.append(new_star)
    config["stars"] = stars
    _save_config(config)

    # Write to database
    from core import db
    db.upsert_star(name=name, handle=handle, code=code)

    invalidate_stars_cache()
    log.info(f"star added: {name} ({code}) from {url}")

    # Background async sync for this actor's titles
    async def _bg_sync() -> None:
        try:
            from scrapers.v2.fetchers import PlaywrightFetcher
            from scrapers.v2.tasks.sync_titles import sync_star
            from scrapers.v2.schemas import StarConfig

            star_cfg = StarConfig(**new_star)
            async with PlaywrightFetcher() as pf:
                sem = asyncio.Semaphore(1)
                result = await sync_star(pf, star_cfg, sem)
                log.info(f"bg sync done: {name}: {result['count']} titles")
                # Titles now written to DB — invalidate cache so next read is fresh
                invalidate_stars_cache()
                # Notify all connected clients
                await publish_event("star.ready", {
                    "code": code,
                    "name": name,
                    "titles_count": result.get("count", 0),
                })
        except Exception as exc:
            log.error(f"bg sync failed: {name}: {exc}")

    asyncio.create_task(_bg_sync())

    return AddStarResponse(
        name=name,
        code=code,
        handle=handle,
        star_page_url=url,
        titles_found=len(items),
    )


# ── Delete Star ─────────────────────────────────────────────────────

@router.delete("/{code}")
async def delete_star(
    request: Request,
    code: str = Path(..., pattern=CODE_PATTERN),
) -> dict[str, Any]:
    """Delete an actor: remove from config.json and DB, clear all title caches for this actor."""
    config = _load_config()
    stars = config.get("stars", [])
    original_len = len(stars)
    config["stars"] = [s for s in stars if s.get("code") != code]

    if len(config["stars"]) == original_len:
        raise HTTPException(status_code=404, detail="Actor not found")

    _save_config(config)

    # Query all magnet hashes for this actor's titles (unavailable after DB deletion)
    engine = request.app.state.engine
    magnet_hashes: list[str] = []
    try:
        conn = duckdb.connect(DB_PATH)
        try:
            rows = conn.execute("""
                SELECT magnet_hash
                FROM titles
                WHERE star_code = ? AND magnet_hash IS NOT NULL
            """, [code]).fetchall()
            magnet_hashes = [h for (h,) in rows if h]
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"delete_star: failed to query magnet hashes for {code}: {e}")

    from core import db
    deleted = db.delete_star_by_code(code)
    if not deleted:
        log.warning(f"star {code} removed from config but not found in db")

    # Clear all caches and tracing bits for this actor's titles
    for hash_str in magnet_hashes:
        try:
            await asyncio.to_thread(engine.remove_torrent, hash_str)
            log.info(f"delete_star: removed torrent {hash_str[:12]}... for {code}")
        except Exception as e:
            log.warning(f"delete_star: failed to remove torrent {hash_str[:12]}... for {code}: {e}")

    invalidate_stars_cache()
    log.info(f"star deleted: {code}")
    return {"code": code, "deleted": True}


# ── Like Title ──────────────────────────────────────────────────────

class LikeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, pattern=CODE_PATTERN)
    liked: bool = True


class LikeResponse(BaseModel):
    code: str
    liked: bool
    downloaded: bool


@router.post("/like")
async def like_title(request: LikeRequest, req: Request) -> LikeResponse:
    """Like / unlike a title; if magnet exists after liking, trigger download immediately."""
    code = request.code.strip().upper()
    liked = request.liked

    conn = duckdb.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT magnet, magnet_hash FROM titles WHERE code = ?",
            [code],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="作品不存在")
        magnet, magnet_hash = row
    finally:
        conn.close()

    # Update database
    conn = duckdb.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE titles SET user_liked = ? WHERE code = ?",
            [1 if liked else 0, code],
        )
        conn.commit()
    finally:
        conn.close()

    invalidate_stars_cache()

    engine = req.app.state.engine
    if magnet_hash:
        await asyncio.to_thread(engine.set_liked, magnet_hash, liked)

    downloaded = False
    if liked and magnet:
        from backend.routers.torrents import _resolve_magnet
        resolved = _resolve_magnet(magnet)
        info = await asyncio.to_thread(engine.add_torrent, resolved, prefetch=False)
        if info:
            hash_str = info["hash"]
            # Start downloading head+moov immediately for liked titles.
            # add_torrent now adds paused; resume_download(0, 0) queues a resume
            # (or applies it directly once metadata is ready) so pieces start
            # fetching instead of staying paused forever.
            await asyncio.to_thread(engine.resume_download, hash_str, 0.0, 0.0)
            downloaded = True
            log.info(f"like_title: auto-download {code} -> {hash_str[:12]}...")
        else:
            log.warning(f"like_title: auto-download failed for {code}")

    # When unliking, immediately clean up downloaded cache
    removed = False
    if not liked and magnet_hash:
        removed = await asyncio.to_thread(engine.remove_torrent, magnet_hash)
        if removed:
            log.info(f"like_title: auto-remove {code} -> {magnet_hash[:12]}...")

    return LikeResponse(
        code=code,
        liked=liked,
        downloaded=downloaded,
    )
