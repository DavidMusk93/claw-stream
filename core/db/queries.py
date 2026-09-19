"""core/db/queries.py — Aggregate queries

After wide-table simplification, no longer JOIN the magnets table; read primary magnet directly from titles.magnet.
"""

from __future__ import annotations

from .connection import _conn
from .ops_log import trace_db


@trace_db
def get_stats() -> dict:
    """Aggregate statistics: total titles, titles per star"""
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
        stars_count = conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0]
        per_star = conn.execute("""
            SELECT s.code, s.name, COUNT(t.id) as title_count,
                   MIN(t.release_date_sort) as earliest,
                   MAX(t.release_date_sort) as latest
            FROM stars s
            LEFT JOIN titles t ON t.star_id = s.id
            GROUP BY s.id, s.code, s.name
            ORDER BY title_count DESC
        """).fetchall()
    finally:
        conn.close()
    return {
        "stars_count": stars_count,
        "titles_total": total,
        "titles_with_jable": 0,
        "jable_coverage": 0.0,
        "social_posts": 0,
        "per_star": [
            {
                "code": r[0],
                "name": r[1],
                "titles": r[2],
                "jable": 0,
                "earliest": r[3],
                "latest": r[4],
            }
            for r in per_star
        ],
    }
