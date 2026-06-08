"""core/db/schema.py — Schema initialization and migration

Wide-table design: the titles table directly inlines star_code, star_name, and magnet info,
completely eliminating stars-titles-magnets triple-table JOINs and cross-table transactions.
"""

from __future__ import annotations

from .connection import _conn


def init_schema(conn=None):
    """Initialize table schema (idempotent)

    conn: if an external connection is passed, use it without closing; otherwise create a new one.
    """
    should_close = conn is None
    conn = conn or _conn()
    try:
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
                star_code TEXT,
                star_name TEXT,
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
                charming_intro TEXT,
                jable_m3u8 TEXT,
                jable_cover TEXT,
                release_date_sort TEXT,
                magnet TEXT,
                magnet_hash TEXT,
                all_magnets JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(star_id, code)
            )
        """)
        # Append columns to existing tables (backward compatibility)
        for col in [
            ("release_date_sort", "TEXT"),
            ("charming_intro", "TEXT"),
            ("star_code", "TEXT"),
            ("star_name", "TEXT"),
            ("magnet", "TEXT"),
            ("magnet_hash", "TEXT"),
            ("all_magnets", "JSON"),
            ("user_liked", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE titles ADD COLUMN {col[0]} {col[1]}")
            except Exception:
                pass
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_date ON titles(release_date_sort)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_jable ON titles(jable_m3u8)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_social_star ON social_posts(star_id)")
        if should_close:
            conn.commit()
    finally:
        if should_close:
            conn.close()


def backfill_release_date_sort():
    """Backfill release_date_sort column for existing data"""
    conn = _conn()
    conn.execute("""
        UPDATE titles
        SET release_date_sort = CONCAT(
            SPLIT_PART(release_date, '/', 3),
            LPAD(SPLIT_PART(release_date, '/', 1), 2, '0'),
            LPAD(SPLIT_PART(release_date, '/', 2), 2, '0')
        )
        WHERE release_date_sort IS NULL
          AND release_date IS NOT NULL
          AND release_date LIKE '%/%/%'
    """)
    conn.commit()
    conn.close()
