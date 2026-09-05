"""scripts/drop_hidden_titles.py — Delete VR / multi-star rows already in the DB.

The sync pipeline now filters hidden titles at collection time
(scrapers/v2/filters.py), but rows synced before that filter still sit in
the DB. This script applies the same rules offline to the stored
title/resolution text — no refetch needed (star_count-based detection is
covered separately by scripts/cleanup_multi_star.py, which re-fetches ijav).

Usage:
    .venv/bin/python scripts/drop_hidden_titles.py           # dry-run report
    .venv/bin/python scripts/drop_hidden_titles.py --apply   # delete rows

--apply opens the DB read-write: stop star-archive-backend first (DuckDB
allows a single writer process). Liked titles (user_liked=1) are never
deleted — they are reported separately. Cached torrents of deleted rows
become orphans; run POST /api/cache/gc-orphans after restarting the backend.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.v2.filters import hidden_reason
from scrapers.v2.schemas import VideoItem

DB_PATH = Path("data/claw.duckdb")
CONFIG_PATH = Path("config.json")
IMAGES_DIR = Path("images/titles")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (stop the backend first)")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        roster = json.load(f).get("stars", [])
    roster_names = [n for s in roster for n in (s.get("name"), s.get("jp")) if n]
    # star_code -> all aliases from config: DB stars.name often stores only the
    # Japanese name, so the romaji alias from config must also count as "own"
    # or titles tagged with the star's own romaji name look like cross-mentions.
    cfg_names_by_code = {
        s.get("code"): [n for n in (s.get("name"), s.get("jp")) if n]
        for s in roster
    }

    conn = duckdb.connect(str(DB_PATH), read_only=not args.apply)
    try:
        star_names = {
            r[0]: [n for n in (r[1], r[2]) if n]
            for r in conn.execute("SELECT id, name, jp_name FROM stars").fetchall()
        }
        rows = conn.execute(
            "SELECT id, star_id, code, title, resolution, user_liked, star_code FROM titles"
        ).fetchall()

        hits: list[tuple[int, str, str, str, str, int]] = []  # row + reason
        for row in rows:
            _id, star_id, code, title, resolution, liked, star_code = row
            item = VideoItem.model_construct(code=code, title=title or "")
            if resolution and "vr" in resolution.lower():
                # Stored resolution is the chosen magnet's tag; hidden_reason
                # only sees magnet-level tags, so check it directly.
                reason = "vr"
            else:
                own = star_names.get(star_id, []) + cfg_names_by_code.get(star_code, [])
                reason = hidden_reason(item, own, roster_names)
            if reason:
                hits.append((_id, code, title or "", reason, star_id, liked))

        if not hits:
            print("no hidden titles found")
            return

        by_reason: dict[str, int] = {}
        for _, code, title, reason, star_id, liked in hits:
            by_reason[reason] = by_reason.get(reason, 0) + 1
            print(f"    - [{reason}] {code} {title[:60]}{' (LIKED, kept)' if liked else ''}")
        print("by reason:", by_reason)

        deletable = [h for h in hits if not h[5]]
        if args.apply and deletable:
            del_ids = [h[0] for h in deletable]
            placeholders = ", ".join(["?"] * len(del_ids))
            conn.execute(f"DELETE FROM titles WHERE id IN ({placeholders})", del_ids)
            conn.commit()
            covers_removed = 0
            for _, code, *_ in deletable:
                cover_dir = IMAGES_DIR / code.lower()
                if cover_dir.is_dir():
                    shutil.rmtree(cover_dir, ignore_errors=True)
                    covers_removed += 1
            print(f"deleted {len(deletable)} hidden titles, removed {covers_removed} cover dirs")
            print("restart the backend, then POST /api/cache/gc-orphans to clear orphaned caches")
        else:
            print(f"dry-run: {len(deletable)} rows would be deleted; pass --apply to execute")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
