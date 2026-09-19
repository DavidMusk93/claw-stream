#!/usr/bin/env python3
"""scripts/migrate_duckdb_to_pg.py — One-shot DuckDB → PostgreSQL data migration.

Reads the live DuckDB file (read-only, safe while the old backend is running)
and copies every table into PostgreSQL via the core.db pool:

    stars → titles (without cover_b64) → title_covers (from titles.cover_b64)
          → social_posts → sync_runs → user_events

Original id values are preserved (IDENTITY BY DEFAULT columns accept explicit
ids) and each identity sequence is recalibrated with setval afterwards.

The script TRUNCATEs all target tables first (idempotent reruns) — pass --yes
to confirm, since this destroys existing target data. Each table copy runs in
its own transaction; any error aborts with a nonzero exit. A reconciliation
report (row counts, titles view sum, cover blob bytes) prints at the end and
must be all-PASS for the cutover to proceed.

Usage:
    export CLAW_PG_DSN=postgresql://claw:***@127.0.0.1:5432/claw
    .venv/bin/python scripts/migrate_duckdb_to_pg.py --yes
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
from psycopg.types.json import Jsonb

DUCKDB_PATH = Path(__file__).resolve().parent.parent / "data" / "claw.duckdb"

# Row counts per insert batch. title_covers rows carry ~200KB base64 blobs,
# so they use a much smaller batch to bound memory on the 4GB box.
BATCH_SIZE = 500
COVER_BATCH_SIZE = 20

# FK-safe truncate order (children first); RESTART IDENTITY + CASCADE make it
# robust regardless.
TRUNCATE_SQL = (
    "TRUNCATE user_events, sync_runs, social_posts, title_covers, titles, stars "
    "RESTART IDENTITY CASCADE"
)

# (table, duckdb SELECT column list, jsonb columns in that list)
TABLES: list[tuple[str, list[str], set[str]]] = [
    (
        "stars",
        ["id", "name", "jp_name", "handle", "code", "type", "note", "created_at", "updated_at"],
        set(),
    ),
    (
        # cover_b64 deliberately excluded — it moves to title_covers.
        "titles",
        [
            "id", "star_id", "star_code", "star_name", "code", "title",
            "release_date", "views", "likes", "resolution", "download_url",
            "cover_url", "cover_path", "charming_intro", "jable_m3u8",
            "jable_cover", "release_date_sort", "magnet", "magnet_hash",
            "all_magnets", "user_liked", "cover_w", "cover_h",
            "magnet_status", "magnet_checked_at", "magnet_checked_hash",
            "created_at", "updated_at",
        ],
        {"all_magnets"},
    ),
    (
        "social_posts",
        ["id", "star_id", "platform", "content", "post_url", "posted_at", "created_at"],
        set(),
    ),
    (
        "sync_runs",
        [
            "id", "trigger", "status", "started_at", "finished_at",
            "total_new", "total_updated", "failed_count", "error",
        ],
        set(),
    ),
    (
        "user_events",
        ["id", "ts", "event", "code", "star_code", "meta"],
        {"meta"},
    ),
]


def _to_jsonb(value):
    """DuckDB returns JSON columns as strings; PG JSONB wants Jsonb objects."""
    if value is None or not isinstance(value, str):
        return Jsonb(value) if value is not None else None
    if not value.strip():
        return None
    return Jsonb(json.loads(value))


def _copy_table(
    duck_conn, pg_conn, table: str, columns: list[str], jsonb_cols: set[str]
) -> int:
    """Copy one table duckdb → pg in batches. Returns rows copied.

    Keyset-paginates by id: DuckDB's execute() materializes the full result
    set, so a plain fetchmany() loop would still buffer the whole table —
    fatal on this 4GB box for blob-carrying tables.
    """
    col_list = ", ".join(columns)
    insert_sql = (
        f"INSERT INTO {table} ({col_list}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})"
    )
    jsonb_idx = {i for i, c in enumerate(columns) if c in jsonb_cols}

    copied = 0
    last_id = 0
    with pg_conn.transaction(), pg_conn.cursor() as cur:
        while True:
            rows = duck_conn.execute(
                f"SELECT {col_list} FROM {table} WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, BATCH_SIZE),
            ).fetchall()
            if not rows:
                break
            last_id = rows[-1][0]
            if jsonb_idx:
                rows = [
                    tuple(_to_jsonb(v) if i in jsonb_idx else v for i, v in enumerate(row))
                    for row in rows
                ]
            cur.executemany(insert_sql, rows)
            copied += len(rows)
            print(f"  {table}: {copied} rows...", flush=True)
    return copied


def _copy_title_covers(duck_conn, pg_conn) -> tuple[int, int]:
    """titles.cover_b64 → title_covers (title_id, cover_b64).

    Two-phase to survive the 4GB box: an ids-only scan first (DuckDB answers
    IS NOT NULL from the validity bitmap without reading blobs), then PK
    point-lookups for COVER_BATCH_SIZE ids at a time, so at most a few MB of
    blob data is in flight at once.
    """
    cover_ids = [
        r[0]
        for r in duck_conn.execute(
            "SELECT id FROM titles WHERE cover_b64 IS NOT NULL ORDER BY id"
        ).fetchall()
    ]

    copied = 0
    total_bytes = 0
    with pg_conn.transaction(), pg_conn.cursor() as cur:
        for i in range(0, len(cover_ids), COVER_BATCH_SIZE):
            chunk = cover_ids[i : i + COVER_BATCH_SIZE]
            placeholders = ", ".join(["?"] * len(chunk))
            rows = duck_conn.execute(
                f"SELECT id, cover_b64 FROM titles WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            cur.executemany(
                "INSERT INTO title_covers (title_id, cover_b64) VALUES (%s, %s)",
                rows,
            )
            copied += len(rows)
            # Accumulate source-side reconciliation stats here so no second
            # blob scan is needed later (each scan re-reads ~450MB).
            total_bytes += sum(len(r[1]) for r in rows)
            del rows
            gc.collect()
            print(f"  title_covers: {copied} rows...", flush=True)
    return copied, total_bytes


def _reset_identity(pg_conn, table: str) -> None:
    """Recalibrate the identity sequence past the explicitly-inserted ids."""
    pg_conn.execute(
        "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
        "COALESCE((SELECT MAX(id) FROM " + table + "), 1))",
        (table,),
    )


def _reconcile(duck_conn, pg_conn, duck_covers: tuple[int, int]) -> bool:
    """Print per-table reconciliation; return True when everything matches.

    duck_covers: (count, total blob bytes) measured during the title_covers
    copy — re-querying the blobs would re-read ~450MB from DuckDB.
    """
    ok = True

    def check(label: str, duck_val, pg_val) -> None:
        nonlocal ok
        status = "PASS" if duck_val == pg_val else "FAIL"
        if duck_val != pg_val:
            ok = False
        print(f"  [{status}] {label}: duckdb={duck_val} pg={pg_val}")

    print("\n── Reconciliation ──")
    for table, _, _ in TABLES:
        d = duck_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        p = pg_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        check(f"{table} rows", d, p)

    d_count, d_views = duck_conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(views), 0) FROM titles"
    ).fetchone()
    p_count, p_views = pg_conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(views), 0) FROM titles"
    ).fetchone()
    check("titles count", d_count, p_count)
    check("titles sum(views)", d_views, p_views)

    d_covers, d_bytes = duck_covers
    p_covers, p_bytes = pg_conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(cover_b64)), 0) FROM title_covers"
    ).fetchone()
    check("covers count", d_covers, p_covers)
    check("covers sum(length(cover_b64))", d_bytes, p_bytes)

    print(f"\nReconciliation: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--duckdb", default=str(DUCKDB_PATH),
        help="path to the source DuckDB file (default: data/claw.duckdb)",
    )
    ap.add_argument(
        "--yes", action="store_true",
        help="confirm truncation of all target PG tables before copying",
    )
    args = ap.parse_args()

    if not os.environ.get("CLAW_PG_DSN"):
        print("error: CLAW_PG_DSN environment variable is not set", file=sys.stderr)
        return 2
    if not args.yes:
        print(
            "This TRUNCATEs all tables in the target PostgreSQL database before\n"
            "copying. Re-run with --yes to proceed.",
            file=sys.stderr,
        )
        return 2
    if not Path(args.duckdb).is_file():
        print(f"error: source DuckDB file not found: {args.duckdb}", file=sys.stderr)
        return 2

    from core import db

    db.init_schema()
    print("target schema initialized")

    duck_conn = duckdb.connect(args.duckdb, read_only=True)
    # Cap DuckDB's buffer manager (defaults to 80% of RAM) — the source file
    # carries ~450MB of cover blobs and this 4GB box also runs the backend,
    # PostgreSQL, and other tenants; keep our footprint small.
    duck_conn.execute("SET memory_limit = '512MB'")
    duck_conn.execute("SET temp_directory = '/tmp/duckdb-spill'")
    duck_conn.execute("SET preserve_insertion_order = false")
    pg_conn = db._conn()
    try:
        pg_conn.execute(TRUNCATE_SQL)
        print("target tables truncated")

        for table, columns, jsonb_cols in TABLES:
            n = _copy_table(duck_conn, pg_conn, table, columns, jsonb_cols)
            _reset_identity(pg_conn, table)
            print(f"{table}: {n} rows copied")

        n, cover_bytes = _copy_title_covers(duck_conn, pg_conn)
        print(f"title_covers: {n} rows copied")

        # Drop copy-phase buffers before the reconciliation queries.
        gc.collect()

        ok = _reconcile(duck_conn, pg_conn, (n, cover_bytes))
        return 0 if ok else 1
    finally:
        pg_conn.close()
        duck_conn.close()
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
