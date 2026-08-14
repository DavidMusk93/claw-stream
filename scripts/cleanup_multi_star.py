"""scripts/cleanup_multi_star.py — Delete existing multi-star (共演/omnibus) titles.

The sync pipeline filters multi-star titles at fetch time (star_count > 1 on
the ijavtorrent card, see sync_titles._drop_multi_star), but rows synced
before that filter still sit in the DB. This script re-fetches each star's
ijavtorrent page, collects codes whose card lists more than one actress,
and deletes the matching title rows (+ their exported cover dirs).

Usage:
    .venv/bin/python scripts/cleanup_multi_star.py           # dry-run report
    .venv/bin/python scripts/cleanup_multi_star.py --apply   # delete rows

--apply opens the DB read-write: stop star-archive-backend first (DuckDB
allows a single writer process). Liked titles (user_liked=1) are never
deleted — they are reported separately. Cached torrents of deleted rows
become orphans; run POST /api/cache/gc-orphans after restarting the backend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.v2.extractors import IJavTorrentExtractor
from scrapers.v2.fetchers import HttpxFetcher

DB_PATH = Path("data/claw.duckdb")
IMAGES_DIR = Path("images/titles")
FETCH_CONCURRENCY = 4


async def collect_multi_codes() -> tuple[dict[str, set[str]], list[str]]:
    """Return ({star_code: {multi-star codes on her ijav page}}, [skipped stars])."""
    with open("config.json", encoding="utf-8") as f:
        stars = json.load(f).get("stars", [])

    multi: dict[str, set[str]] = {}
    skipped: list[str] = []
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def _one(fetcher: HttpxFetcher, star: dict) -> None:
        url = star.get("star_page_url", "")
        name = star.get("name", "?")
        if not url:
            skipped.append(f"{name} (no star_page_url)")
            return
        try:
            async with sem:
                html = await fetcher.fetch(url)
        except Exception as e:
            # Never delete blindly: a star whose page cannot be fetched is skipped.
            skipped.append(f"{name} (fetch failed: {type(e).__name__})")
            return
        items = IJavTorrentExtractor().extract(html)
        codes = {it.code for it in items if it.star_count > 1}
        multi[star["code"]] = codes
        print(f"  {name}: {len(items)} titles on page, {len(codes)} multi-star")

    async with HttpxFetcher() as fetcher:
        await asyncio.gather(*[_one(fetcher, s) for s in stars])
    return multi, skipped


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (stop the backend first)")
    args = ap.parse_args()

    print("fetching ijavtorrent pages...")
    multi, skipped = await collect_multi_codes()
    if skipped:
        print(f"SKIPPED (no deletion): {', '.join(skipped)}")

    conn = duckdb.connect(str(DB_PATH), read_only=not args.apply)
    try:
        # A title that is multi-star is 混演 no matter whose library it sits
        # in: ijav listings are sparse, so a compilation may only show on one
        # co-star's current page. Delete by the GLOBAL multi-code set.
        all_multi = set().union(*multi.values()) if multi else set()
        if not all_multi:
            print("no multi-star codes found")
            return
        placeholders = ", ".join(["?"] * len(all_multi))
        rows = conn.execute(
            f"SELECT star_code, code, title, user_liked FROM titles "
            f"WHERE code IN ({placeholders}) ORDER BY star_code, code",
            sorted(all_multi),
        ).fetchall()

        total_delete = 0
        liked_kept: list[str] = []
        covers_removed = 0
        deletable = [r for r in rows if not r[3]]
        liked_kept = [f"{r[1]} ({r[0]}, liked)" for r in rows if r[3]]
        by_star: dict[str, int] = {}
        for star_code, code, title, _ in deletable:
            by_star[star_code] = by_star.get(star_code, 0) + 1
            print(f"    - [{star_code}] {code} {title[:50]}")
        for star_code, n in sorted(by_star.items()):
            print(f"  {star_code}: {n} to delete")

        if args.apply and deletable:
            del_codes = sorted({r[1] for r in deletable})
            del_placeholders = ", ".join(["?"] * len(del_codes))
            conn.execute(
                f"DELETE FROM titles WHERE user_liked = 0 AND code IN ({del_placeholders})",
                del_codes,
            )
            total_delete = len(deletable)
            for code in del_codes:
                cover_dir = IMAGES_DIR / code
                if cover_dir.is_dir():
                    shutil.rmtree(cover_dir, ignore_errors=True)
                    covers_removed += 1
        if args.apply:
            conn.commit()
            print(f"deleted {total_delete} multi-star titles, removed {covers_removed} cover dirs")
            print("restart the backend, then POST /api/cache/gc-orphans to clear orphaned caches")
        else:
            print(f"dry-run: {len(deletable)} rows would be deleted; pass --apply to execute")
        if liked_kept:
            print(f"liked titles kept ({len(liked_kept)}): {', '.join(liked_kept)}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
