"""core/db/crud.py — Basic CRUD operations

After wide-table simplification:
- The titles table inlines magnet info directly, no separate magnets table is maintained
- upsert_title adds star_code / star_name / magnet fields
- Remove all magnets-related CRUD
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

from PIL import Image

from core.logger import get_logger
from .connection import _conn, _date_to_sort
from .ops_log import trace_db

log = get_logger("db-crud")


SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
IMAGES_DIR = SCRIPT_DIR / "images" / "titles"
JPEG_QUALITY = 90


def _normalize_b64_to_jpeg(b64_data: str) -> bytes | None:
    """Decode base64 cover and normalize to JPEG bytes."""
    if not b64_data:
        return None
    if b64_data.startswith("data:image/"):
        b64_data = b64_data.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:
        return None


def _safe_code(code: str) -> str:
    """Normalize and validate a title code for filesystem use."""
    if not code:
        raise ValueError("empty code")
    code_lower = code.lower()
    if not re.fullmatch(r"[a-z0-9_-]+", code_lower):
        raise ValueError(f"invalid code for filesystem: {code}")
    return code_lower


def _write_cover_to_disk(code: str, cover_b64: str | None) -> None:
    """Persist cover_b64 to images/titles/{code}/{code}.jpg for static serving."""
    if not cover_b64:
        return
    try:
        code_lower = _safe_code(code)
    except ValueError as exc:
        log.warning(f"cover write skipped: {exc}")
        return
    out_dir = IMAGES_DIR / code_lower
    out_path = out_dir / f"{code_lower}.jpg"
    if out_path.exists() and out_path.stat().st_size > 0:
        return
    jpeg_bytes = _normalize_b64_to_jpeg(cover_b64)
    if not jpeg_bytes:
        return
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(jpeg_bytes)
    except Exception:
        log.warning(f"cover write failed for {code}", exc_info=True)


def _extract_hash(magnet: str | None) -> str | None:
    if not magnet:
        return None
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
def upsert_stars(star_rows, conn=None):
    """Batch upsert stars, returns {code: id}. Single connection + commit."""
    managed, should_close = _managed_conn(conn)
    try:
        mapping = {}
        for row in star_rows:
            mapping[row["code"]] = upsert_star(
                name=row.get("name"),
                jp_name=row.get("jp_name"),
                handle=row.get("handle"),
                code=row.get("code"),
                type=row.get("type"),
                note=row.get("note"),
                conn=managed,
            )
        if should_close:
            managed.commit()
        return mapping
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


# Resolutions that count as a proper HD source. [4K]/[8KVR]/[4KVR] included:
# makers hhd800 never covers (FALENO/DAHLIA max out at 4K uploads, VR tops at
# 8KVR) must not be re-backfilled forever.
HD_RESOLUTIONS = ("[FHD]", "[FHDC]", "[8KVR]", "[4KVR]", "[4K]")


@trace_db
def load_title_codes_missing_metadata(conn=None) -> set[tuple[int, str]]:
    """Load (star_id, code) for titles missing critical metadata fields.

    Also flags titles whose magnet candidates contain no HD-resolution
    source (e.g. synced from the narrow RSS window during the 2026-08
    ijavtorrent outage, before the hhd800 upload rotated out) — the hybrid
    sync backfills their magnets when a source lists them again.
    """
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            """
            SELECT star_id, code FROM titles
            WHERE title IS NULL OR title = ''
               OR release_date IS NULL OR release_date = ''
               OR star_code IS NULL OR star_code = ''
               OR star_name IS NULL OR star_name = ''
               OR magnet IS NULL OR magnet = ''
            """
        ).fetchall()
        result = set(rows)

        magnet_rows = managed.execute(
            "SELECT star_id, code, all_magnets FROM titles WHERE all_magnets IS NOT NULL"
        ).fetchall()
        for star_id, code, all_magnets in magnet_rows:
            try:
                mags = json.loads(all_magnets)
            except (TypeError, ValueError):
                continue
            if not any(m.get("resolution") in HD_RESOLUTIONS for m in mags):
                result.add((star_id, code))
        return result
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
                json.dumps(all_magnets) if all_magnets is not None else None,
            ))
            row = managed.execute(
                "SELECT id FROM titles WHERE star_id = ? AND code = ?",
                (star_id, code)
            ).fetchone()
            result = row[0]
        if should_close:
            managed.commit()
        # Keep disk cache in sync so new/edited covers are served immediately.
        _write_cover_to_disk(code, cover_b64)
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


# ── Sync run history ────────────────────────────────────────────────

@trace_db
def insert_sync_run(trigger: str, conn=None) -> int:
    """Start a sync run record, returns its id."""
    managed, should_close = _managed_conn(conn)
    try:
        managed.execute(
            "INSERT INTO sync_runs (trigger) VALUES (?)", (trigger,)
        )
        row = managed.execute("SELECT max(id) FROM sync_runs").fetchone()
        if should_close:
            managed.commit()
        return row[0]
    finally:
        if should_close:
            managed.close()


@trace_db
def finish_sync_run(
    run_id: int,
    status: str,
    total_new: int = 0,
    total_updated: int = 0,
    failed_count: int = 0,
    error: str | None = None,
    conn=None,
) -> None:
    """Mark a sync run finished/failed with its outcome."""
    managed, should_close = _managed_conn(conn)
    try:
        managed.execute(
            """
            UPDATE sync_runs SET
                status = ?, finished_at = now(), total_new = ?,
                total_updated = ?, failed_count = ?, error = ?
            WHERE id = ?
            """,
            (status, total_new, total_updated, failed_count, error, run_id),
        )
        if should_close:
            managed.commit()
    finally:
        if should_close:
            managed.close()


@trace_db
def list_sync_runs(limit: int = 10, conn=None) -> list[dict]:
    """Recent sync runs, newest first."""
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            """
            SELECT id, trigger, status,
                   strftime(started_at, '%Y-%m-%d %H:%M:%S'),
                   strftime(finished_at, '%Y-%m-%d %H:%M:%S'),
                   total_new, total_updated, failed_count, error
            FROM sync_runs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0], "trigger": r[1], "status": r[2],
                "started_at": r[3], "finished_at": r[4],
                "total_new": r[5], "total_updated": r[6],
                "failed_count": r[7], "error": r[8],
            }
            for r in rows
        ]
    finally:
        if should_close:
            managed.close()


# ── User behavior events (埋点) ─────────────────────────────────────

@trace_db
def insert_user_events(events: list[dict], conn=None) -> int:
    """Batch insert user behavior events. Returns inserted count."""
    if not events:
        return 0
    managed, should_close = _managed_conn(conn)
    try:
        managed.executemany(
            "INSERT INTO user_events (event, code, star_code, meta) VALUES (?, ?, ?, ?)",
            [
                (
                    str(e.get("event", ""))[:64],
                    (e.get("code") or None),
                    (e.get("star_code") or None),
                    json.dumps(e.get("meta")) if e.get("meta") is not None else None,
                )
                for e in events[:100]
            ],
        )
        if should_close:
            managed.commit()
        return min(len(events), 100)
    finally:
        if should_close:
            managed.close()
