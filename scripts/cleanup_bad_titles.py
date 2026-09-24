"""scripts/cleanup_bad_titles.py — Delete titles with unusable magnet/cover.

The sync sink now rejects these at write time (scrapers/v2/sinks.py):
- bad_magnet: magnet missing, or no extractable 40-hex btih hash
- tall_cover: cover taller than wide (h/w > COVER_MAX_HW_RATIO) — vertical
  front-cover thumbnails that break aspect-ratio rendering
- no_cover: no title_covers blob AND no disk file under images/titles/

Rows synced before those rules still sit in the DB. This script applies the
same rules offline — no refetch needed.

Usage:
    .venv/bin/python scripts/cleanup_bad_titles.py           # dry-run report
    .venv/bin/python scripts/cleanup_bad_titles.py --apply   # delete rows

Liked titles (user_liked=1) are never deleted — they are reported separately.
Cached torrents of deleted rows become orphans; run POST /api/cache/gc-orphans
to clear them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import _conn
from scrapers.v2.sinks import COVER_MAX_HW_RATIO, _extract_hash

IMAGES_DIR = Path("images/titles")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (stop the backend first)")
    args = ap.parse_args()

    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.star_id, t.code, t.cover_w, t.cover_h, t.magnet,
                   t.user_liked, (length(tc.cover_b64) > 0) AS has_blob
            FROM titles t
            LEFT JOIN title_covers tc ON tc.title_id = t.id
            """
        ).fetchall()

        # row: (id, star_id, code, reason, liked)
        hits: list[tuple[int, int, str, str, int]] = []
        for _id, star_id, code, cover_w, cover_h, magnet, liked, has_blob in rows:
            reason = None
            if cover_w and cover_h and cover_h / cover_w > COVER_MAX_HW_RATIO:
                reason = "tall_cover"
            elif not _extract_hash(magnet):
                reason = "bad_magnet"
            elif not has_blob and not (IMAGES_DIR / code.lower() / f"{code.lower()}.jpg").is_file():
                reason = "no_cover"
            if reason:
                hits.append((_id, star_id, code, reason, liked))

        if not hits:
            print("no bad titles found")
            return

        by_reason: dict[str, int] = {}
        for _, _, code, reason, liked in hits:
            by_reason[reason] = by_reason.get(reason, 0) + 1
            print(f"    - [{reason}] {code}{' (LIKED, kept)' if liked else ''}")
        print("by reason:", by_reason)

        deletable = [h for h in hits if not h[4]]
        if args.apply and deletable:
            del_ids = [h[0] for h in deletable]
            placeholders = ", ".join(["%s"] * len(del_ids))
            with conn.transaction():
                # Seal deleted codes in the blacklist so the next sync does
                # not re-download their covers just to reject them again.
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
                        [(h[1], h[2], h[3]) for h in deletable],
                    )
                conn.execute(f"DELETE FROM titles WHERE id IN ({placeholders})", del_ids)
            covers_removed = 0
            for _, _, code, *_ in deletable:
                cover_dir = IMAGES_DIR / code.lower()
                if cover_dir.is_dir():
                    shutil.rmtree(cover_dir, ignore_errors=True)
                    covers_removed += 1
            print(f"deleted {len(deletable)} bad titles, removed {covers_removed} cover dirs")
            print("run POST /api/cache/gc-orphans to clear orphaned caches")
        else:
            print(f"dry-run: {len(deletable)} rows would be deleted; pass --apply to execute")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
