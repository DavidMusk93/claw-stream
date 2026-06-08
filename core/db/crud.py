"""core/db/crud.py — Basic CRUD operations

After wide-table simplification:
- The titles table inlines magnet info directly, no separate magnets table is maintained
- upsert_title adds star_code / star_name / magnet fields
- Remove all magnets-related CRUD
"""

from __future__ import annotations

import json
import re
from .connection import _conn, _date_to_sort
from .ops_log import trace_db


def _extract_hash(magnet):
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else None


def _managed_conn(conn=None):
    """Connection management helper.

    If an external connection is passed, returns (conn, False)—caller is not responsible for closing.
    If not passed, creates a new connection and returns (new_conn, True)—caller must commit/close.
    """
    if conn is not None:
        return conn, False
    return _conn(), True


@trace_db
def upsert_star(name, jp_name=None, handle=None, code=None, type=None, note=None, conn=None):
    """Insert or update star info, returns id"""
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
    """Check if title already exists"""
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
    """Load all (star_id, code) into memory set at once for batch existence checks"""
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
    """Insert or update title info.

    In wide-table mode, magnet info is written directly along with the title,
    all candidates stored via the all_magnets JSON column, magnet/magnet_hash stores the primary.
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
            # Preserve existing cover_b64 (do not overwrite during incremental refresh)
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

    Returns whether the actor was found and deleted.
    """
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute("SELECT id FROM stars WHERE code = ?", (code,)).fetchone()
        if not row:
            return False
        star_id = row[0]

        # Delete in foreign-key dependency order: social_posts → titles → stars
        managed.execute("DELETE FROM social_posts WHERE star_id = ?", (star_id,))
        managed.execute("DELETE FROM titles WHERE star_id = ?", (star_id,))
        managed.execute("DELETE FROM stars WHERE id = ?", (star_id,))
        if should_close:
            managed.commit()
        return True
    finally:
        if should_close:
            managed.close()
