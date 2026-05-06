#!/usr/bin/env python3
"""db.py — DuckDB 持久化层

存储 actress、works、magnets 的完整数据，避免重复抓取。
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actresses (
            id INTEGER PRIMARY KEY,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY,
            actress_id INTEGER NOT NULL,
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
            FOREIGN KEY (actress_id) REFERENCES actresses(id),
            UNIQUE(actress_id, code)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS magnets (
            id INTEGER PRIMARY KEY,
            work_id INTEGER NOT NULL,
            magnet TEXT NOT NULL,
            hash TEXT,
            is_primary BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (work_id) REFERENCES works(id),
            UNIQUE(work_id, hash)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_works_actress ON works(actress_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_works_code ON works(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_works_date ON works(release_date)")
    conn.commit()
    conn.close()


def _extract_hash(magnet):
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else None


def upsert_actress(name, jp_name=None, handle=None, code=None, type=None, note=None):
    """插入或更新演员信息，返回 id"""
    conn = _conn()
    conn.execute("""
        INSERT INTO actresses (name, jp_name, handle, code, type, note)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (name) DO UPDATE SET
            jp_name = excluded.jp_name,
            handle = excluded.handle,
            code = excluded.code,
            type = excluded.type,
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
    """, (name, jp_name, handle, code, type, note))
    row = conn.execute("SELECT id FROM actresses WHERE name = ?", (name,)).fetchone()
    conn.commit()
    conn.close()
    return row[0]


def work_exists(actress_id, code):
    """检查作品是否已存在"""
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM works WHERE actress_id = ? AND code = ?",
        (actress_id, code)
    ).fetchone()
    conn.close()
    return row is not None


def upsert_work(actress_id, code, title=None, release_date=None, views=None,
                likes=None, resolution=None, download_url=None, cover_url=None,
                cover_b64=None, cover_path=None):
    """插入或更新作品信息"""
    conn = _conn()
    conn.execute("""
        INSERT INTO works (actress_id, code, title, release_date, views, likes,
                           resolution, download_url, cover_url, cover_b64, cover_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (actress_id, code) DO UPDATE SET
            title = excluded.title,
            release_date = excluded.release_date,
            views = excluded.views,
            likes = excluded.likes,
            resolution = excluded.resolution,
            download_url = excluded.download_url,
            cover_url = excluded.cover_url,
            cover_b64 = excluded.cover_b64,
            cover_path = excluded.cover_path,
            updated_at = CURRENT_TIMESTAMP
    """, (actress_id, code, title, release_date, views, likes,
          resolution, download_url, cover_url, cover_b64, cover_path))
    row = conn.execute(
        "SELECT id FROM works WHERE actress_id = ? AND code = ?",
        (actress_id, code)
    ).fetchone()
    conn.commit()
    conn.close()
    return row[0]


def upsert_magnet(work_id, magnet, is_primary=True):
    """插入或更新磁力链接"""
    h = _extract_hash(magnet)
    if not h:
        return
    conn = _conn()
    conn.execute("""
        INSERT INTO magnets (work_id, magnet, hash, is_primary)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (work_id, hash) DO UPDATE SET
            magnet = excluded.magnet,
            is_primary = excluded.is_primary
    """, (work_id, magnet, h, is_primary))
    conn.commit()
    conn.close()


def update_jable(work_id, m3u8_url=None, cover_url=None):
    """更新 jable 数据"""
    conn = _conn()
    conn.execute("""
        UPDATE works SET jable_m3u8 = ?, jable_cover = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (m3u8_url, cover_url, work_id))
    conn.commit()
    conn.close()


def get_works_without_jable(actress_name=None):
    """获取缺少 jable 数据的作品列表"""
    conn = _conn()
    if actress_name:
        rows = conn.execute("""
            SELECT w.id, w.code, w.title, a.name
            FROM works w
            JOIN actresses a ON w.actress_id = a.id
            WHERE a.name = ? AND w.jable_m3u8 IS NULL
            ORDER BY w.release_date DESC
        """, (actress_name,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT w.id, w.code, w.title, a.name
            FROM works w
            JOIN actresses a ON w.actress_id = a.id
            WHERE w.jable_m3u8 IS NULL
            ORDER BY w.release_date DESC
        """).fetchall()
    conn.close()
    return rows


def get_all_works_json():
    """导出所有数据为 JSON 格式（兼容旧 generate-report.js）

    返回: { "actresses": [ { name, works: [...] } ] }
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
            w.code as work_code,
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
        FROM actresses a
        LEFT JOIN works w ON w.actress_id = a.id
        LEFT JOIN magnets m ON m.work_id = w.id AND m.is_primary = true
        ORDER BY a.name, w.release_date DESC
    """).fetchall()
    conn.close()

    # 聚合成 actress -> works 结构
    actress_map = {}
    for row in result:
        name = row[0]
        if name not in actress_map:
            actress_map[name] = {
                "name": name,
                "jp_name": row[1],
                "handle": row[2],
                "code": row[3],
                "type": row[4] or "solo",
                "note": row[5],
                "works": []
            }
        if row[6]:  # work_code
            actress_map[name]["works"].append({
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

    return {"actresses": list(actress_map.values())}


def export_report_json():
    """导出为 generate-report.js 直接消费的 JSON（stdout）

    格式: { "<actress_code>": { "name": "...", "works": [...] } }
    每个 work 已合并 ijavtorrent + jable 数据。
    """
    conn = _conn()
    result = conn.execute("""
        SELECT
            a.code as actress_code,
            a.name,
            w.code as work_code,
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
        FROM actresses a
        LEFT JOIN works w ON w.actress_id = a.id
        LEFT JOIN magnets m ON m.work_id = w.id AND m.is_primary = true
        ORDER BY a.name, w.release_date DESC
    """).fetchall()
    conn.close()

    data = {}
    for row in result:
        actress_code = row[0]
        if actress_code not in data:
            data[actress_code] = {"name": row[1], "works": []}
        if row[2]:  # work_code
            data[actress_code]["works"].append({
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
