#!/usr/bin/env python3
"""scripts/fix_broken_magnets.py — Repair HTML-escaped magnets in DuckDB.

Root cause: ijavtorrent embeds magnet hrefs double-escaped (`&amp;amp;dn=`),
and the extractor used to unescape only once, persisting `&amp;`-joined
magnets. The broken `amp;dn` / `amp;tr` parameter names silently dropped the
display name and all trackers (DHT-only downloads).

This one-off script unescapes `magnet` and `all_magnets` in place.
Idempotent: rows without `&amp;` are untouched.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import db


def fix_broken_magnets() -> int:
    db.init_schema()
    conn = db._conn()
    try:
        broken = conn.execute(
            "SELECT count(*) FROM titles "
            "WHERE magnet LIKE '%&amp;%' OR all_magnets LIKE '%&amp;%'"
        ).fetchone()[0]
        if broken:
            conn.execute(
                """
                UPDATE titles
                SET magnet = replace(magnet, '&amp;', '&'),
                    all_magnets = replace(all_magnets, '&amp;', '&'),
                    updated_at = now()
                WHERE magnet LIKE '%&amp;%' OR all_magnets LIKE '%&amp;%'
                """
            )
            conn.commit()
        return broken
    finally:
        conn.close()


if __name__ == "__main__":
    n = fix_broken_magnets()
    print(f"fixed {n} titles")
