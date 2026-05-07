#!/usr/bin/env python3
"""db.py — DuckDB 持久化层

存储 star、titles、magnets 的完整数据，避免重复抓取。
不变数据（作品信息、封面 base64、jable 数据）写入后永驻，
增量刷新只抓取新增作品。
"""

import os, sys, json, re, datetime, glob
import duckdb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return duckdb.connect(DB_PATH)


def init_schema():
    """初始化表结构（幂等）"""
    conn = _conn()
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_star_id START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stars (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_star_id'),
            name TEXT NOT NULL UNIQUE,
            jp_name TEXT,
            handle TEXT,
            code TEXT,
            type TEXT DEFAULT 'solo',
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_title_id START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS titles (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_title_id'),
            star_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            title TEXT,
            release_date TEXT,
            views INTEGER,
            likes INTEGER,
            resolution TEXT,
            download_url TEXT,
            cover_url TEXT,
            cover_b64 TEXT,
            cover_path TEXT,
            jable_m3u8 TEXT,
            jable_cover TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- FK removed: DuckDB UPDATE bug with FK constraints
            UNIQUE(star_id, code)
        )
    """)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_magnet_id START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS magnets (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_magnet_id'),
            title_id INTEGER NOT NULL,
            magnet TEXT NOT NULL,
            hash TEXT,
            is_primary BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- FK removed: DuckDB UPDATE bug with FK constraints
            UNIQUE(title_id, hash)
        )
    """)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_social_id START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS social_posts (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_social_id'),
            star_id INTEGER NOT NULL,
            platform TEXT NOT NULL DEFAULT 'x',
            content TEXT NOT NULL,
            post_url TEXT,
            posted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (star_id) REFERENCES stars(id),
            UNIQUE(star_id, platform, content)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_star ON titles(star_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_code ON titles(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_date ON titles(release_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_social_star ON social_posts(star_id)")
    conn.commit()
    conn.close()


def _extract_hash(magnet):
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else None


def upsert_star(name, jp_name=None, handle=None, code=None, type=None, note=None):
    """插入或更新 star 信息，返回 id"""
    conn = _conn()
    row = conn.execute("SELECT id FROM stars WHERE name = ?", (name,)).fetchone()
    if row:
        conn.execute("""
            UPDATE stars SET
                jp_name = ?,
                handle = ?,
                code = ?,
                type = ?,
                note = ?,
                updated_at = now()
            WHERE id = ?
        """, (jp_name, handle, code, type, note, row[0]))
        conn.commit()
        conn.close()
        return row[0]
    conn.execute("""
        INSERT INTO stars (name, jp_name, handle, code, type, note)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, jp_name, handle, code, type, note))
    row = conn.execute("SELECT id FROM stars WHERE name = ?", (name,)).fetchone()
    conn.commit()
    conn.close()
    return row[0]


def title_exists(star_id, code):
    """检查 title 是否已存在"""
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM titles WHERE star_id = ? AND code = ?",
        (star_id, code)
    ).fetchone()
    conn.close()
    return row is not None


def upsert_title(star_id, code, title=None, release_date=None, views=None,
                likes=None, resolution=None, download_url=None, cover_url=None,
                cover_b64=None, cover_path=None):
    """插入或更新 title 信息"""
    conn = _conn()
    row = conn.execute(
        "SELECT id, cover_b64 FROM titles WHERE star_id = ? AND code = ?",
        (star_id, code)
    ).fetchone()
    if row:
        title_id, existing_cover = row[0], row[1]
        # 保留已有 cover_b64（增量刷新时不覆盖）
        if existing_cover and cover_b64 is None:
            cover_b64 = existing_cover
        conn.execute("""
            UPDATE titles SET
                title = ?,
                release_date = ?,
                views = ?,
                likes = ?,
                resolution = ?,
                download_url = ?,
                cover_url = ?,
                cover_b64 = ?,
                cover_path = ?,
                updated_at = now()
            WHERE id = ?
        """, (title, release_date, views, likes, resolution,
              download_url, cover_url, cover_b64, cover_path, title_id))
        conn.commit()
        conn.close()
        return title_id
    conn.execute("""
        INSERT INTO titles (star_id, code, title, release_date, views, likes,
                           resolution, download_url, cover_url, cover_b64, cover_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (star_id, code, title, release_date, views, likes,
          resolution, download_url, cover_url, cover_b64, cover_path))
    row = conn.execute(
        "SELECT id FROM titles WHERE star_id = ? AND code = ?",
        (star_id, code)
    ).fetchone()
    conn.commit()
    conn.close()
    return row[0]


def upsert_magnet(title_id, magnet, is_primary=True):
    """插入或更新磁力链接"""
    h = _extract_hash(magnet)
    if not h:
        return
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM magnets WHERE title_id = ? AND hash = ?",
        (title_id, h)
    ).fetchone()
    if row:
        conn.execute("""
            UPDATE magnets SET magnet = ?, is_primary = ?
            WHERE title_id = ? AND hash = ?
        """, (magnet, is_primary, title_id, h))
    else:
        conn.execute("""
            INSERT INTO magnets (title_id, magnet, hash, is_primary)
            VALUES (?, ?, ?, ?)
        """, (title_id, magnet, h, is_primary))
    conn.commit()
    conn.close()


def update_jable(title_id, m3u8_url=None, cover_url=None):
    """更新 jable 数据"""
    conn = _conn()
    conn.execute("""
        UPDATE titles SET jable_m3u8 = ?, jable_cover = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (m3u8_url, cover_url, title_id))
    conn.commit()
    conn.close()


def upsert_social_post(star_id, platform, content, post_url=None, posted_at=None):
    """插入或更新社交动态"""
    conn = _conn()
    row = conn.execute(
        "SELECT id FROM social_posts WHERE star_id = ? AND platform = ? AND content = ?",
        (star_id, platform, content)
    ).fetchone()
    if row:
        conn.execute("""
            UPDATE social_posts SET post_url = ?, posted_at = ?
            WHERE id = ?
        """, (post_url, posted_at, row[0]))
        conn.commit()
        conn.close()
        return row[0]
    conn.execute("""
        INSERT INTO social_posts (star_id, platform, content, post_url, posted_at)
        VALUES (?, ?, ?, ?, ?)
    """, (star_id, platform, content, post_url, posted_at))
    row = conn.execute(
        "SELECT id FROM social_posts WHERE star_id = ? AND platform = ? AND content = ?",
        (star_id, platform, content)
    ).fetchone()
    conn.commit()
    conn.close()
    return row[0]


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
            ORDER BY w.release_date DESC
        """, (star_name,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT w.id, w.code, w.title, a.name
            FROM titles w
            JOIN stars a ON w.star_id = a.id
            WHERE w.jable_m3u8 IS NULL
            ORDER BY w.release_date DESC
        """).fetchall()
    conn.close()
    return rows


def get_all_titles_json():
    """导出所有数据为 JSON 格式（兼容旧 generate-report.js）

    返回: { "stars": [ { name, titles: [...] } ] }
    """
    conn = _conn()
    result = conn.execute("""
        SELECT
            a.name,
            a.jp_name,
            a.handle,
            a.code,
            a.type,
            a.note,
            w.code as title_code,
            w.title,
            w.release_date,
            w.views,
            w.likes,
            w.resolution,
            w.download_url,
            w.cover_url,
            w.cover_b64,
            w.cover_path,
            w.jable_m3u8,
            w.jable_cover,
            m.magnet
        FROM stars a
        LEFT JOIN titles w ON w.star_id = a.id
        LEFT JOIN magnets m ON m.title_id = w.id AND m.is_primary = true
        ORDER BY a.name, w.release_date DESC
    """).fetchall()
    conn.close()

    # 聚合成 star -> titles 结构
    star_map = {}
    for row in result:
        name = row[0]
        if name not in star_map:
            star_map[name] = {
                "name": name,
                "jp_name": row[1],
                "handle": row[2],
                "code": row[3],
                "type": row[4] or "solo",
                "note": row[5],
                "titles": []
            }
        if row[6]:  # title_code
            star_map[name]["titles"].append({
                "code": row[6],
                "title": row[7],
                "date": row[8],
                "views": str(row[9]) if row[9] else "",
                "likes": str(row[10]) if row[10] else "",
                "resolution": row[11] or "",
                "download_url": row[12] or "",
                "cover_url": row[13] or "",
                "cover_b64": row[14] or "",
                "cover_path": row[15] or "",
                "m3u8_url": row[16] or "",
                "jable_cover": row[17] or "",
                "magnet": row[18] or "",
            })

    return {"stars": list(star_map.values())}


def export_report_json():
    """导出为 generate-report.js 直接消费的 JSON（stdout）

    格式: { "<star_code>": { "name": "...", "titles": [...] } }
    每个 title 已合并 ijavtorrent + jable 数据。
    """
    conn = _conn()
    result = conn.execute("""
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

    data = {}
    for row in result:
        star_code = row[0]
        if star_code not in data:
            data[star_code] = {"name": row[1], "titles": []}
        if row[2]:  # title_code
            data[star_code]["titles"].append({
                "code": row[2],
                "title": row[3],
                "date": row[4],
                "views": str(row[5]) if row[5] else "",
                "likes": str(row[6]) if row[6] else "",
                "resolution": row[7] or "",
                "download_url": row[8] or "",
                "cover_url": row[9] or "",
                "cover_b64": row[10] or "",
                "m3u8_url": row[11] or "",
                "jable_cover": row[12] or "",
                "magnet": row[13] or "",
            })

    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export_to_tmp":
        print("[db] export_to_tmp removed; generate-report.js reads DuckDB directly via Node.js driver")
    else:
        init_schema()
        print(f"[db] initialized: {DB_PATH}")
