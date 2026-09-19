"""One-off: rebuild claw.duckdb into a fresh file to reclaim bloat.

Old file: data/claw.duckdb (15G, ~450MB real data). New file is verified
row-by-row against the old one, then swapped in by the caller.

Usage: .venv/bin/python scripts/rebuild_db.py
Requires the backend to be stopped (single-writer lock).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

from core.db.connection import _apply_pragmas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = os.path.join(ROOT, "data", "claw.duckdb")
NEW = os.path.join(ROOT, "data", "claw_rebuilt.duckdb")
TABLES = ["stars", "titles", "social_posts", "sync_runs", "user_events"]


def main() -> None:
    if os.path.exists(NEW):
        os.remove(NEW)
    con = duckdb.connect()  # in-memory coordinator; old/new attached by path
    _apply_pragmas(con)     # enforces 1GB memory limit + spill dir
    con.execute(f"ATTACH '{OLD}' AS old_db (READ_ONLY)")
    con.execute(f"ATTACH '{NEW}' AS new_db")
    con.execute("COPY FROM DATABASE old_db TO new_db")
    con.commit()

    ok = True
    for t in TABLES:
        old_n = con.execute(f"SELECT COUNT(*) FROM old_db.{t}").fetchone()[0]
        new_n = con.execute(f"SELECT COUNT(*) FROM new_db.{t}").fetchone()[0]
        status = "ok" if old_n == new_n else "MISMATCH"
        if old_n != new_n:
            ok = False
        print(f"{t}: old={old_n} new={new_n} {status}")
    con.close()

    old_sz = os.path.getsize(OLD) / 2**30
    new_sz = os.path.getsize(NEW) / 2**30
    print(f"size: {old_sz:.1f}G -> {new_sz:.2f}G")
    if not ok:
        sys.exit("row count mismatch — NOT swapping")
    print("verify ok; swap with:")
    print(f"  mv {OLD} {OLD}.bak-20260919 && mv {NEW} {OLD}")


if __name__ == "__main__":
    main()
