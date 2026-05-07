"""core/db/schema.py — Schema 初始化与迁移"""

from .connection import _conn


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
            release_date_sort TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(star_id, code)
        )
    """)
    # 为已存在表追加 release_date_sort 列
    try:
        conn.execute("ALTER TABLE titles ADD COLUMN release_date_sort TEXT")
    except Exception:
        pass
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_magnet_id START 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS magnets (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_magnet_id'),
            title_id INTEGER NOT NULL,
            magnet TEXT NOT NULL,
            hash TEXT,
            is_primary BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_date ON titles(release_date_sort)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_jable ON titles(jable_m3u8)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_magnets_title ON magnets(title_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_social_star ON social_posts(star_id)")
    conn.commit()
    conn.close()


def backfill_release_date_sort():
    """回填已有数据的 release_date_sort 列"""
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
