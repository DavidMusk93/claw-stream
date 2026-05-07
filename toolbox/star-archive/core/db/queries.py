"""core/db/queries.py — 聚合查询与导出"""

import json
from .connection import _conn


def get_social_posts(star_id, limit=3):
    """获取 star 最近动态"""
    conn = _conn()
    rows = conn.execute("""
        SELECT platform, content, post_url, posted_at
        FROM social_posts
        WHERE star_id = ?
        ORDER BY COALESCE(posted_at, created_at) DESC
        LIMIT ?
    """, (star_id, limit)).fetchall()
    conn.close()
    return rows


def get_titles_without_jable(star_name=None):
    """获取缺少 jable 数据的 title 列表"""
    conn = _conn()
    if star_name:
        rows = conn.execute("""
            SELECT w.id, w.code, w.title, a.name
            FROM titles w
            JOIN stars a ON w.star_id = a.id
            WHERE a.name = ? AND w.jable_m3u8 IS NULL
            ORDER BY w.release_date_sort DESC NULLS LAST
        """, (star_name,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT w.id, w.code, w.title, a.name
            FROM titles w
            JOIN stars a ON w.star_id = a.id
            WHERE w.jable_m3u8 IS NULL
            ORDER BY w.release_date_sort DESC NULLS LAST
        """).fetchall()
    conn.close()
    return rows


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
                m3u8_url := IFNULL(w.jable_m3u8, ''),
                jable_cover := IFNULL(w.jable_cover, ''),
                magnet := IFNULL(m.magnet, '')
            ) ORDER BY w.release_date_sort DESC NULLS LAST)
            FILTER (WHERE w.code IS NOT NULL), []) AS titles
        FROM stars a
        LEFT JOIN titles w ON w.star_id = a.id
        LEFT JOIN magnets m ON m.title_id = w.id AND m.is_primary = true
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
                m3u8_url := IFNULL(w.jable_m3u8, ''),
                jable_cover := IFNULL(w.jable_cover, ''),
                magnet := IFNULL(m.magnet, '')
            ) ORDER BY w.release_date_sort DESC NULLS LAST)
            FILTER (WHERE w.code IS NOT NULL), []) AS titles
        FROM stars a
        LEFT JOIN titles w ON w.star_id = a.id
        LEFT JOIN magnets m ON m.title_id = w.id AND m.is_primary = true
        GROUP BY a.id, a.code, a.name
        ORDER BY a.name
    """).fetchall()
    conn.close()

    data = {r[0]: {"name": r[1], "titles": r[2]} for r in rows}
    print(json.dumps(data, ensure_ascii=False))


def get_stats() -> dict:
    """聚合统计：作品总数、jable 覆盖率、动态条数、各 star 作品数"""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
    jable = conn.execute("SELECT COUNT(*) FROM titles WHERE jable_m3u8 IS NOT NULL").fetchone()[0]
    social = conn.execute("SELECT COUNT(*) FROM social_posts").fetchone()[0]
    stars_count = conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0]
    per_star = conn.execute("""
        SELECT s.code, s.name, COUNT(t.id) as title_count,
               COUNT(t.jable_m3u8) as jable_count,
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
        "titles_with_jable": jable,
        "jable_coverage": round(jable / total, 3) if total else 0.0,
        "social_posts": social,
        "per_star": [
            {
                "code": r[0],
                "name": r[1],
                "titles": r[2],
                "jable": r[3],
                "earliest": r[4],
                "latest": r[5],
            }
            for r in per_star
        ],
    }
