"""core/db/crud.py — 基础增删改查操作

大宽表简化后：
- titles 表直接内联 magnet 信息，不再维护单独的 magnets 表
- upsert_title 增加 star_code / star_name / magnet 字段
- 删除所有 magnets 相关 CRUD
"""

import json
import re
from .connection import _conn, _date_to_sort
from .ops_log import trace_db


def _extract_hash(magnet):
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else None


def _managed_conn(conn=None):
    """连接管理助手。

    若传入外部连接，返回 (conn, False)——调用方不负责关闭。
    若未传入，新建连接并返回 (new_conn, True)——调用方需 commit/close。
    """
    if conn is not None:
        return conn, False
    return _conn(), True


@trace_db
def upsert_star(name, jp_name=None, handle=None, code=None, type=None, note=None, conn=None):
    """插入或更新 star 信息，返回 id"""
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute("SELECT id FROM stars WHERE name = ?", (name,)).fetchone()
        if row:
            managed.execute("""
                UPDATE stars SET
                    jp_name = ?,
                    handle = ?,
                    code = ?,
                    type = ?,
                    note = ?,
                    updated_at = now()
                WHERE id = ?
            """, (jp_name, handle, code, type, note, row[0]))
            result = row[0]
        else:
            managed.execute("""
                INSERT INTO stars (name, jp_name, handle, code, type, note)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, jp_name, handle, code, type, note))
            row = managed.execute("SELECT id FROM stars WHERE name = ?", (name,)).fetchone()
            result = row[0]
        if should_close:
            managed.commit()
        return result
    finally:
        if should_close:
            managed.close()


@trace_db
def title_exists(star_id, code, conn=None):
    """检查 title 是否已存在"""
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute(
            "SELECT 1 FROM titles WHERE star_id = ? AND code = ?",
            (star_id, code)
        ).fetchone()
        return row is not None
    finally:
        if should_close:
            managed.close()


@trace_db
def load_all_title_codes(conn=None) -> set[tuple[int, str]]:
    """一次性加载所有 (star_id, code) 到内存 set，用于批量存在性判断"""
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute("SELECT star_id, code FROM titles").fetchall()
        return set(rows)
    finally:
        if should_close:
            managed.close()


@trace_db
def upsert_title(
    star_id,
    code,
    title=None,
    release_date=None,
    views=None,
    likes=None,
    resolution=None,
    download_url=None,
    cover_url=None,
    cover_b64=None,
    cover_path=None,
    star_code=None,
    star_name=None,
    magnet=None,
    magnet_hash=None,
    all_magnets=None,
    conn=None,
):
    """插入或更新 title 信息。

    大宽表模式下，magnet 信息直接随 title 一起写入，
    通过 all_magnets JSON 列存储所有候选，magnet/magnet_hash 存储 primary。
    """
    release_date_sort = _date_to_sort(release_date)
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute(
            "SELECT id, cover_b64 FROM titles WHERE star_id = ? AND code = ?",
            (star_id, code)
        ).fetchone()
        if row:
            title_id, existing_cover = row[0], row[1]
            # 保留已有 cover_b64（增量刷新时不覆盖）
            if existing_cover and cover_b64 is None:
                cover_b64 = existing_cover
            managed.execute("""
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
                    star_code = ?,
                    star_name = ?,
                    magnet = COALESCE(?, magnet),
                    magnet_hash = COALESCE(?, magnet_hash),
                    all_magnets = COALESCE(?, all_magnets),
                    updated_at = now()
                WHERE id = ?
            """, (
                title, release_date, release_date_sort, views, likes, resolution,
                download_url, cover_url, cover_b64, cover_path,
                star_code, star_name, magnet, magnet_hash, all_magnets, title_id
            ))
            result = title_id
        else:
            managed.execute("""
                INSERT INTO titles (star_id, code, title, release_date, release_date_sort,
                                   views, likes, resolution, download_url, cover_url,
                                   cover_b64, cover_path, star_code, star_name,
                                   magnet, magnet_hash, all_magnets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                star_id, code, title, release_date, release_date_sort, views, likes,
                resolution, download_url, cover_url, cover_b64, cover_path,
                star_code, star_name, magnet, magnet_hash,
                json.dumps(all_magnets) if all_magnets else None,
            ))
            row = managed.execute(
                "SELECT id FROM titles WHERE star_id = ? AND code = ?",
                (star_id, code)
            ).fetchone()
            result = row[0]
        if should_close:
            managed.commit()
        return result
    finally:
        if should_close:
            managed.close()


@trace_db
def delete_star_by_code(code: str, conn=None) -> bool:
    """Delete an actor and all associated data (titles, social_posts).

    返回是否成功找到并删除。
    """
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute("SELECT id FROM stars WHERE code = ?", (code,)).fetchone()
        if not row:
            return False
        star_id = row[0]

        # 按外键依赖顺序删除：social_posts → titles → stars
        managed.execute("DELETE FROM social_posts WHERE star_id = ?", (star_id,))
        managed.execute("DELETE FROM titles WHERE star_id = ?", (star_id,))
        managed.execute("DELETE FROM stars WHERE id = ?", (star_id,))
        if should_close:
            managed.commit()
        return True
    finally:
        if should_close:
            managed.close()
