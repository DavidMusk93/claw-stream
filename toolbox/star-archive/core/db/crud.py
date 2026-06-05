"""core/db/crud.py — 基础增删改查操作"""

import re
from .connection import _conn, _date_to_sort
from .ops_log import trace_db


def _extract_hash(magnet):
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else None


@trace_db
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


@trace_db
def title_exists(star_id, code):
    """检查 title 是否已存在"""
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM titles WHERE star_id = ? AND code = ?",
        (star_id, code)
    ).fetchone()
    conn.close()
    return row is not None


@trace_db
def load_all_title_codes() -> set[tuple[int, str]]:
    """一次性加载所有 (star_id, code) 到内存 set，用于批量存在性判断"""
    conn = _conn()
    rows = conn.execute("SELECT star_id, code FROM titles").fetchall()
    conn.close()
    return set(rows)


@trace_db
def upsert_title(star_id, code, title=None, release_date=None, views=None,
                likes=None, resolution=None, download_url=None, cover_url=None,
                cover_b64=None, cover_path=None):
    """插入或更新 title 信息"""
    release_date_sort = _date_to_sort(release_date)
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
                release_date_sort = ?,
                views = ?,
                likes = ?,
                resolution = ?,
                download_url = ?,
                cover_url = ?,
                cover_b64 = ?,
                cover_path = ?,
                updated_at = now()
            WHERE id = ?
        """, (title, release_date, release_date_sort, views, likes, resolution,
              download_url, cover_url, cover_b64, cover_path, title_id))
        conn.commit()
        conn.close()
        return title_id
    conn.execute("""
        INSERT INTO titles (star_id, code, title, release_date, release_date_sort, views, likes,
                           resolution, download_url, cover_url, cover_b64, cover_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (star_id, code, title, release_date, release_date_sort, views, likes,
          resolution, download_url, cover_url, cover_b64, cover_path))
    row = conn.execute(
        "SELECT id FROM titles WHERE star_id = ? AND code = ?",
        (star_id, code)
    ).fetchone()
    conn.commit()
    conn.close()
    return row[0]


@trace_db
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


@trace_db
def delete_star_by_code(code: str) -> bool:
    """删除女优及其所有关联数据（titles, magnets, social_posts）。

    返回是否成功找到并删除。
    """
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM stars WHERE code = ?", (code,)).fetchone()
        if not row:
            conn.close()
            return False
        star_id = row[0]

        # 获取该 star 的所有 title ids
        title_rows = conn.execute(
            "SELECT id FROM titles WHERE star_id = ?", (star_id,)
        ).fetchall()
        title_ids = [r[0] for r in title_rows]

        # 按外键依赖顺序删除：social_posts → magnets → titles → stars
        conn.execute("DELETE FROM social_posts WHERE star_id = ?", (star_id,))
        if title_ids:
            placeholders = ", ".join(["?"] * len(title_ids))
            conn.execute(f"DELETE FROM magnets WHERE title_id IN ({placeholders})", title_ids)
            conn.execute("DELETE FROM titles WHERE star_id = ?", (star_id,))

        conn.execute("DELETE FROM stars WHERE id = ?", (star_id,))
        conn.commit()
        return True
    finally:
        conn.close()
