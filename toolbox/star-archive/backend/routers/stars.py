from __future__ import annotations

import json
import os
from fastapi import APIRouter
from typing import Any

import duckdb

router = APIRouter(prefix="/api/stars", tags=["stars"])

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


def _get_db() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH)


def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("")
async def get_stars():
    """Get all stars with their latest titles and posts (SQL-aggregated)."""
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
                cover_b64 := IFNULL(r.cover_b64, ''),
                m3u8_url := IFNULL(r.jable_m3u8, ''),
                jable_cover := IFNULL(r.jable_cover, ''),
                magnet := IFNULL(m.magnet, '')
            )) FILTER (WHERE r.code IS NOT NULL), []) AS titles
        FROM stars s
        LEFT JOIN ranked r ON r.star_id = s.id AND r.rn <= 3
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
            )) FILTER (WHERE r.content IS NOT NULL), []) AS posts
        FROM stars s
        LEFT JOIN ranked r ON r.star_id = s.id AND r.rn <= 3
        GROUP BY s.id, s.code, s.name
    """).fetchall()

    conn.close()

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
            "titles": data.get("titles", []),
            "posts": posts,
        })

    # Sort by latest title date ascending
    def _latest_date(star):
        titles = star.get("titles", [])
        if titles and titles[0].get("date"):
            d = titles[0]["date"]
            if d and "/" in d:
                parts = d.split("/")
                return f"{parts[2]}{parts[0].zfill(2)}{parts[1].zfill(2)}"
        return "99999999"

    result.sort(key=_latest_date)
    return result
