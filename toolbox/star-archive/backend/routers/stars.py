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
    """Get all stars with their latest titles."""
    config = _load_config()
    solo = [a for a in config.get("stars", []) if not a.get("type") or a.get("type") == "solo"]

    conn = _get_db()
    rows = conn.execute("""
        SELECT
            a.code as star_code,
            a.name,
            w.code as title_code,
            w.title,
            w.release_date,
            w.views,
            w.likes,
            w.resolution,
            w.download_url,
            w.cover_url,
            w.cover_b64,
            w.jable_m3u8,
            w.jable_cover,
            m.magnet
        FROM stars a
        LEFT JOIN titles w ON w.star_id = a.id
        LEFT JOIN magnets m ON m.title_id = w.id AND m.is_primary = true
        ORDER BY a.name, w.release_date DESC
    """).fetchall()
    conn.close()

    db_data: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row[0]
        if code not in db_data:
            db_data[code] = {"name": row[1], "titles": [], "posts": []}
        if row[2]:  # title_code
            db_data[code]["titles"].append({
                "code": row[2],
                "title": row[3],
                "date": row[4] or "",
                "views": str(row[5] or ""),
                "likes": str(row[6] or ""),
                "resolution": row[7] or "",
                "download_url": row[8] or "",
                "cover_url": row[9] or "",
                "cover_b64": row[10] or "",
                "m3u8_url": row[11] or "",
                "jable_cover": row[12] or "",
                "magnet": row[13] or "",
            })

    # Query social posts
    conn = _get_db()
    post_rows = conn.execute("""
        SELECT a.code as star_code, s.platform, s.content, s.post_url, s.posted_at
        FROM social_posts s
        JOIN stars a ON s.star_id = a.id
        ORDER BY COALESCE(s.posted_at, s.created_at) DESC
    """).fetchall()
    conn.close()

    for row in post_rows:
        code = row[0]
        if code not in db_data:
            db_data[code] = {"name": "", "titles": [], "posts": []}
        db_data[code]["posts"].append({
            "platform": row[1],
            "content": row[2],
            "url": row[3] or "",
            "posted_at": row[4] or "",
        })

    result = []
    for a in solo:
        code = a["code"]
        data = db_data.get(code, {"titles": [], "posts": []})
        titles = data.get("titles", [])
        # Sort by date desc and limit to 3
        titles.sort(key=lambda w: w.get("date", "").split("/")[::-1] if w.get("date") else "", reverse=True)
        latest_titles = titles[:3]

        # Deduplicate posts, max 3
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
            "titles": latest_titles,
            "posts": posts,
        })

    # Sort by latest work date ascending
    def _latest_date(star):
        titles = star.get("titles", [])
        if titles and titles[0].get("date"):
            return titles[0]["date"].split("/")[::-1]
        return "99999999"

    result.sort(key=_latest_date)
    return result
