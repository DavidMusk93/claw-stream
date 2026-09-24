"""core/db/crud.py — Basic CRUD operations

After wide-table simplification:
- The titles table inlines magnet info directly, no separate magnets table is maintained
- Cover blobs live in the title_covers table (1:1 with titles)
- Remove all magnets-related CRUD
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image
from psycopg.types.json import Jsonb

from core.logger import get_logger
from .connection import _conn
from .ops_log import trace_db

log = get_logger("db-crud")


SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
IMAGES_DIR = SCRIPT_DIR / "images" / "titles"
JPEG_QUALITY = 90


def _cover_dims_from_b64(b64_data: str | None) -> tuple[int, int] | None:
    """Decode base64 cover and return (width, height), or None."""
    if not b64_data:
        return None
    if b64_data.startswith("data:image/"):
        b64_data = b64_data.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64_data)
        with Image.open(io.BytesIO(raw)) as img:
            return img.width, img.height
    except Exception:
        return None


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
        _write_thumb(code_lower, jpeg_bytes)
    except Exception:
        log.warning(f"cover write failed for {code}", exc_info=True)


THUMB_WIDTH = 400
THUMB_QUALITY = 82


def _write_thumb(code_lower: str, full_jpeg: bytes) -> None:
    """Generate {code}_thumb.jpg (THUMB_WIDTH px wide) next to the full cover.

    List/grid views load the thumb variant; skipping this on write leaves new
    titles downloading the full-size cover until the next manual export run.
    """
    thumb_path = IMAGES_DIR / code_lower / f"{code_lower}_thumb.jpg"
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return
    img = Image.open(io.BytesIO(full_jpeg))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    if img.width > THUMB_WIDTH:
        height = round(img.height * THUMB_WIDTH / img.width)
        img = img.resize((THUMB_WIDTH, height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    thumb_path.write_bytes(out.getvalue())


def _extract_hash(magnet: str | None) -> str | None:
    if not magnet:
        return None
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else None


def _managed_conn(conn=None):
    """Connection management helper.

    If an external connection is passed, returns (conn, False)—caller is not responsible for closing.
    If not passed, checks out a pooled connection and returns (conn, True)—caller must close it
    (close() returns the connection to the pool). Pool connections are autocommit, so no
    commit() is needed for single statements.
    """
    if conn is not None:
        return conn, False
    return _conn(), True


@trace_db
def upsert_star(name, jp_name=None, handle=None, code=None, type=None, note=None, conn=None):
    """Insert or update star info, returns id"""
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute("SELECT id FROM stars WHERE name = %s", (name,)).fetchone()
        if row:
            managed.execute("""
                UPDATE stars SET
                    jp_name = %s,
                    handle = %s,
                    code = %s,
                    type = %s,
                    note = %s,
                    updated_at = now()
                WHERE id = %s
            """, (jp_name, handle, code, type, note, row[0]))
            result = row[0]
        else:
            row = managed.execute("""
                INSERT INTO stars (name, jp_name, handle, code, type, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (name, jp_name, handle, code, type, note)).fetchone()
            result = row[0]
        return result
    finally:
        if should_close:
            managed.close()


@trace_db
def upsert_stars(star_rows, conn=None):
    """Batch upsert stars, returns {code: id}. Single pooled connection."""
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
        return mapping
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


def _parse_json_column(value):
    """Normalize a JSON/JSONB column value: psycopg3 returns parsed objects
    for JSONB, while older call sites/tests may still hand us a raw string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


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
            mags = _parse_json_column(all_magnets)
            if not isinstance(mags, list):
                continue
            if not any(isinstance(m, dict) and m.get("resolution") in HD_RESOLUTIONS for m in mags):
                result.add((star_id, code))
        return result
    finally:
        if should_close:
            managed.close()


@trace_db
def load_title_codes_missing_cover(conn=None) -> set[tuple[int, str]]:
    """Load (star_id, code) for titles with no usable cover.

    Uses cover_w IS NULL as a cheap proxy — cover blobs live in the separate
    title_covers table and are never scanned here.
    """
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            "SELECT star_id, code FROM titles WHERE cover_w IS NULL"
        ).fetchall()
        return set(rows)
    finally:
        if should_close:
            managed.close()


@trace_db
def load_blacklisted_codes(retry_days: int = 7, conn=None) -> set[tuple[int, str]]:
    """Load (star_id, code) pairs currently on the title blacklist.

    Entries older than ``retry_days`` are not returned: they get one retry
    pass in the next sync (the source may have fixed the cover/magnet), and
    if they are still rejected the sink re-records them and ``last_seen``
    refreshes, blacklisting them for another window.
    """
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            "SELECT star_id, code FROM title_blacklist"
            " WHERE last_seen > now() - make_interval(days => %s)",
            (retry_days,),
        ).fetchall()
        return set(rows)
    finally:
        if should_close:
            managed.close()


def record_blacklist_skips(
    conn, star_id: int, skips: list[tuple[str, str]]
) -> None:
    """Upsert (code, reason) skip records into title_blacklist.

    Meant to be called inside the caller's transaction (no autocommit
    concerns here). Repeated skips bump skip_count and refresh last_seen.
    """
    if not skips:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO title_blacklist (star_id, code, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (star_id, code) DO UPDATE SET
                reason = EXCLUDED.reason,
                skip_count = title_blacklist.skip_count + 1,
                last_seen = now()
            """,
            [(star_id, code, reason) for code, reason in skips],
        )


def clear_blacklist_entries(conn, star_id: int, codes: list[str]) -> None:
    """Remove codes from title_blacklist after they were written successfully
    (a retry-window pass that finally produced a usable cover + magnet)."""
    if not codes:
        return
    conn.execute(
        "DELETE FROM title_blacklist WHERE star_id = %s AND code = ANY(%s)",
        (star_id, codes),
    )


@trace_db
def delete_star_by_code(code: str, conn=None) -> bool:
    """Delete an actor and all associated data (titles, social_posts).

    Returns whether the actor was found and deleted.
    """
    managed, should_close = _managed_conn(conn)
    try:
        row = managed.execute("SELECT id FROM stars WHERE code = %s", (code,)).fetchone()
        if not row:
            return False
        star_id = row[0]

        # Delete in foreign-key dependency order: social_posts → titles → stars
        # (title_covers rows cascade with their titles)
        managed.execute("DELETE FROM social_posts WHERE star_id = %s", (star_id,))
        managed.execute("DELETE FROM titles WHERE star_id = %s", (star_id,))
        managed.execute("DELETE FROM stars WHERE id = %s", (star_id,))
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
        row = managed.execute(
            "INSERT INTO sync_runs (trigger) VALUES (%s) RETURNING id", (trigger,)
        ).fetchone()
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
                status = %s, finished_at = now(), total_new = %s,
                total_updated = %s, failed_count = %s, error = %s
            WHERE id = %s
            """,
            (status, total_new, total_updated, failed_count, error, run_id),
        )
    finally:
        if should_close:
            managed.close()


@trace_db
def list_sync_runs(limit: int = 10, conn=None) -> list[dict]:
    """Recent sync runs, newest first. Timestamps are real datetime objects."""
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            """
            SELECT id, trigger, status, started_at, finished_at,
                   total_new, total_updated, failed_count, error
            FROM sync_runs ORDER BY id DESC LIMIT %s
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
        with managed.cursor() as cur:
            cur.executemany(
                "INSERT INTO user_events (event, code, star_code, meta) VALUES (%s, %s, %s, %s)",
                [
                    (
                        str(e.get("event", ""))[:64],
                        (e.get("code") or None),
                        (e.get("star_code") or None),
                        Jsonb(e.get("meta")) if e.get("meta") is not None else None,
                    )
                    for e in events[:100]
                ],
            )
        return min(len(events), 100)
    finally:
        if should_close:
            managed.close()


# ── Magnet liveness check ───────────────────────────────────────────

@trace_db
def load_titles_for_magnet_check(scope: str = "changed", conn=None) -> list[dict]:
    """Load titles whose primary magnet needs a liveness check.

    scope:
      - unchecked: never checked, or primary hash changed since the last check
      - dead:      currently marked dead (retry)
      - all:       every title with a magnet (full historical sweep)
      - changed:   unchecked OR dead (post-sync default)
    """
    where = "magnet IS NOT NULL AND magnet != ''"
    hash_changed = "magnet_hash IS DISTINCT FROM magnet_checked_hash"
    if scope == "unchecked":
        where += f" AND (magnet_checked_at IS NULL OR {hash_changed})"
    elif scope == "dead":
        where += " AND magnet_status = 'dead'"
    elif scope == "changed":
        where += f" AND (magnet_checked_at IS NULL OR {hash_changed} OR magnet_status = 'dead')"
    elif scope != "all":
        raise ValueError(f"unknown magnet check scope: {scope}")

    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            f"SELECT id, code, magnet, magnet_hash, all_magnets FROM titles WHERE {where}"
        ).fetchall()
        return [
            {"id": r[0], "code": r[1], "magnet": r[2], "magnet_hash": r[3], "all_magnets": r[4]}
            for r in rows
        ]
    finally:
        if should_close:
            managed.close()


@trace_db
def update_magnet_check_ok(title_id: int, checked_hash: str, conn=None) -> None:
    """Mark a title's primary magnet alive."""
    managed, should_close = _managed_conn(conn)
    try:
        managed.execute(
            """
            UPDATE titles SET magnet_status = 'ok', magnet_checked_at = now(),
                magnet_checked_hash = %s, updated_at = now()
            WHERE id = %s
            """,
            (checked_hash, title_id),
        )
    finally:
        if should_close:
            managed.close()


@trace_db
def update_magnet_check_dead(title_id: int, checked_hash: str | None, conn=None) -> None:
    """Mark a title's primary magnet dead (no live candidate found)."""
    managed, should_close = _managed_conn(conn)
    try:
        managed.execute(
            """
            UPDATE titles SET magnet_status = 'dead', magnet_checked_at = now(),
                magnet_checked_hash = %s, updated_at = now()
            WHERE id = %s
            """,
            (checked_hash, title_id),
        )
    finally:
        if should_close:
            managed.close()


@trace_db
def swap_primary_magnet(title_id: int, new_magnet: str, new_hash: str, conn=None) -> None:
    """Promote a live candidate from all_magnets to the primary magnet."""
    managed, should_close = _managed_conn(conn)
    try:
        managed.execute(
            """
            UPDATE titles SET magnet = %s, magnet_hash = %s,
                magnet_status = 'ok', magnet_checked_at = now(),
                magnet_checked_hash = %s, updated_at = now()
            WHERE id = %s
            """,
            (new_magnet, new_hash, new_hash, title_id),
        )
    finally:
        if should_close:
            managed.close()


@trace_db
def list_unplayable_titles(conn=None) -> list[dict]:
    """Titles that can never play: dead-checked magnets or no magnet at all."""
    managed, should_close = _managed_conn(conn)
    try:
        rows = managed.execute(
            """
            SELECT id, code, COALESCE(user_liked, 0) FROM titles
            WHERE magnet_status = 'dead' OR magnet IS NULL OR magnet = ''
            """
        ).fetchall()
        return [{"id": r[0], "code": r[1], "user_liked": r[2]} for r in rows]
    finally:
        if should_close:
            managed.close()


@trace_db
def delete_titles_by_ids(ids: list[int], conn=None) -> int:
    """Delete title rows by id. Returns deleted count."""
    if not ids:
        return 0
    managed, should_close = _managed_conn(conn)
    try:
        placeholders = ", ".join(["%s"] * len(ids))
        managed.execute(f"DELETE FROM titles WHERE id IN ({placeholders})", ids)
        return len(ids)
    finally:
        if should_close:
            managed.close()
