from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from typing import Any

import duckdb

router = APIRouter(prefix="/api/stars", tags=["stars"])

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# 持久化 DuckDB 连接（避免每次请求 36ms 连接开销）
_db_conn: duckdb.DuckDBPyConnection | None = None


def _get_db() -> duckdb.DuckDBPyConnection:
    global _db_conn
    if _db_conn is None:
        _db_conn = duckdb.connect(DB_PATH, read_only=True)
    return _db_conn


def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# 内存缓存: { data, ts }
_stars_cache: dict[str, Any] = {"data": None, "ts": 0}
CACHE_TTL = 5  # 秒 — 缩短 TTL，同步完成后几乎立即生效


def invalidate_stars_cache() -> None:
    """清除 stars 内存缓存，供同步完成后调用。"""
    global _stars_cache
    _stars_cache = {"data": None, "ts": 0}


def _build_stars_response() -> list[dict[str, Any]]:
    """构建 stars 响应（无缓存逻辑，纯数据组装）"""
    config = _load_config()
    solo = [a for a in config.get("stars", []) if not a.get("type") or a.get("type") == "solo"]

    conn = _get_db()

    # Query 1: titles aggregated per star (top 3 by date)
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
                m3u8_url := IFNULL(r.jable_m3u8, ''),
                jable_cover := IFNULL(r.jable_cover, ''),
                charming_intro := IFNULL(r.charming_intro, ''),
                magnet := IFNULL(m.magnet, '')
            ) ORDER BY r.rn) FILTER (WHERE r.code IS NOT NULL), []) AS titles
        FROM stars s
        LEFT JOIN ranked r ON r.star_id = s.id AND r.rn <= 5
        LEFT JOIN magnets m ON m.title_id = r.id AND m.is_primary = true
        GROUP BY s.id, s.code, s.name
        ORDER BY s.name
    """).fetchall()

    # Query 2: posts aggregated per star (top 3 by date)
    post_rows = conn.execute("""
        WITH ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY star_id
                    ORDER BY COALESCE(posted_at, created_at) DESC
                ) AS rn
            FROM social_posts
        )
        SELECT
            s.code,
            COALESCE(array_agg(struct_pack(
                platform := r.platform,
                content := r.content,
                url := IFNULL(r.post_url, ''),
                posted_at := IFNULL(CAST(r.posted_at AS VARCHAR), '')
            ) ORDER BY r.rn) FILTER (WHERE r.content IS NOT NULL), []) AS posts
        FROM stars s
        LEFT JOIN ranked r ON r.star_id = s.id AND r.rn <= 5
        GROUP BY s.id, s.code, s.name
    """).fetchall()

    db_data: dict[str, dict[str, Any]] = {}
    for row in title_rows:
        db_data[row[0]] = {"titles": row[1], "posts": []}
    for row in post_rows:
        code = row[0]
        if code not in db_data:
            db_data[code] = {"titles": [], "posts": []}
        db_data[code]["posts"] = row[1]

    result = []
    for a in solo:
        code = a["code"]
        data = db_data.get(code, {"titles": [], "posts": []})

        # Rewrite cover_url to proxy URL (bypass CDN referer restriction)
        titles = data.get("titles", [])
        for t in titles:
            if t.get("cover_url"):
                t["cover_url"] = f"/api/cover/{t['code']}"

        # Deduplicate posts by content (defensive)
        seen = set()
        posts = []
        for p in data.get("posts", []):
            if p["content"] not in seen:
                seen.add(p["content"])
                posts.append(p)
                if len(posts) >= 3:
                    break

        result.append({
            "name": a["name"],
            "jp": a.get("jp", ""),
            "handle": a.get("handle", ""),
            "code": code,
            "type": a.get("type", "solo"),
            "note": a.get("note", ""),
            "titles": titles,
            "posts": posts,
        })

    # Sort stars by latest title date descending (newest star first)
    def _latest_date(star):
        titles = star.get("titles", [])
        if titles and titles[0].get("date"):
            d = titles[0]["date"]
            if d and "/" in d:
                parts = d.split("/")
                return f"{parts[2]}{parts[1].zfill(2)}{parts[0].zfill(2)}"
        return "99999999"

    result.sort(key=_latest_date, reverse=True)

    # Assign global numbers to all titles in star order (star[0] -> #1,2,3, star[1] -> #4,5,6...)
    number = 1
    for star in result:
        for t in star.get("titles", []):
            t["number"] = number
            number += 1

    return result


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
