"""core/db/queries.py — 聚合查询与导出

大宽表简化后，不再 JOIN magnets 表，直接从 titles.magnet 读取 primary magnet。
"""

import json
from .connection import _conn
from .ops_log import trace_db


@trace_db
def get_all_titles_json():
    """导出所有数据为 JSON 格式（SQL 层聚合）

    返回: { "stars": [ { name, titles: [...] } ] }
    """
    conn = _conn()
    rows = conn.execute("""
        SELECT
            a.name,
            a.jp_name,
            a.handle,
            a.code,
            a.type,
            a.note,
            COALESCE(array_agg(struct_pack(
                code := w.code,
                title := w.title,
                date := IFNULL(w.release_date, ''),
                views := IFNULL(CAST(w.views AS VARCHAR), ''),
                likes := IFNULL(CAST(w.likes AS VARCHAR), ''),
                resolution := IFNULL(w.resolution, ''),
                download_url := IFNULL(w.download_url, ''),
                cover_url := IFNULL(w.cover_url, ''),
                cover_b64 := IFNULL(w.cover_b64, ''),
                cover_path := IFNULL(w.cover_path, ''),
                magnet := IFNULL(w.magnet, '')
            ) ORDER BY w.release_date_sort DESC NULLS LAST)
            FILTER (WHERE w.code IS NOT NULL), []) AS titles
        FROM stars a
        LEFT JOIN titles w ON w.star_id = a.id
        GROUP BY a.id, a.name, a.jp_name, a.handle, a.code, a.type, a.note
        ORDER BY a.name
    """).fetchall()
    conn.close()

    return {"stars": [
        {
            "name": r[0],
            "jp_name": r[1],
            "handle": r[2],
            "code": r[3],
            "type": r[4] or "solo",
            "note": r[5],
            "titles": r[6],
        }
        for r in rows
    ]}


@trace_db
def export_report_json():
    """导出为 JSON（stdout），SQL 层聚合。

    格式: { "<star_code>": { "name": "...", "titles": [...] } }
    """
    conn = _conn()
    rows = conn.execute("""
        SELECT
            a.code,
            a.name,
            COALESCE(array_agg(struct_pack(
                code := w.code,
                title := w.title,
                date := IFNULL(w.release_date, ''),
                views := IFNULL(CAST(w.views AS VARCHAR), ''),
                likes := IFNULL(CAST(w.likes AS VARCHAR), ''),
                resolution := IFNULL(w.resolution, ''),
                download_url := IFNULL(w.download_url, ''),
                cover_url := IFNULL(w.cover_url, ''),
                cover_b64 := IFNULL(w.cover_b64, ''),
                magnet := IFNULL(w.magnet, '')
            ) ORDER BY w.release_date_sort DESC NULLS LAST)
            FILTER (WHERE w.code IS NOT NULL), []) AS titles
        FROM stars a
        LEFT JOIN titles w ON w.star_id = a.id
        GROUP BY a.id, a.code, a.name
        ORDER BY a.name
    """).fetchall()
    conn.close()

    data = {r[0]: {"name": r[1], "titles": r[2]} for r in rows}
    print(json.dumps(data, ensure_ascii=False))


@trace_db
def get_stats() -> dict:
    """聚合统计：作品总数、各 star 作品数"""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
    stars_count = conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0]
    per_star = conn.execute("""
        SELECT s.code, s.name, COUNT(t.id) as title_count,
               MIN(t.release_date_sort) as earliest,
               MAX(t.release_date_sort) as latest
        FROM stars s
        LEFT JOIN titles t ON t.star_id = s.id
        GROUP BY s.id, s.code, s.name
        ORDER BY title_count DESC
    """).fetchall()
    conn.close()
    return {
        "stars_count": stars_count,
        "titles_total": total,
        "titles_with_jable": 0,
        "jable_coverage": 0.0,
        "social_posts": 0,
        "per_star": [
            {
                "code": r[0],
                "name": r[1],
                "titles": r[2],
                "jable": 0,
                "earliest": r[3],
                "latest": r[4],
            }
            for r in per_star
        ],
    }
