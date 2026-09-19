#!/usr/bin/env python3
"""Fix undersized cover images in the database.

Scans title_covers (blobs fetched one row at a time to bound memory),
re-downloads covers that fail is_good_cover, and upserts title_covers.

Usage: cd /root/claw-stream && python3 scripts/fix_bad_covers.py
"""
from __future__ import annotations

import sys, os, base64, asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import db
from scrapers.v2.cover_utils import download_cover_b64, parse_image_size, is_good_cover


def main():
    conn = db._conn()

    # Find all covers (id list only; blobs are fetched per row below)
    rows = conn.execute("""
        SELECT t.id, t.code, t.cover_url
        FROM titles t
        JOIN title_covers c ON c.title_id = t.id
        WHERE c.cover_b64 IS NOT NULL
    """).fetchall()

    bad = []
    for title_id, code, cover_url in rows:
        row = conn.execute(
            "SELECT cover_b64 FROM title_covers WHERE title_id = %s", (title_id,)
        ).fetchone()
        if not row or not row[0]:
            continue
        b64 = row[0]
        if b64.startswith('data:image/'):
            b64 = b64.split(',', 1)[1]
        try:
            data = base64.b64decode(b64)
        except Exception:
            bad.append((title_id, code, cover_url))
            print(f"BAD: {code}: undecodable base64")
            continue
        w, h = parse_image_size(data)
        if not is_good_cover(data):
            bad.append((title_id, code, cover_url))
            print(f"BAD: {code}: {w}x{h}, {len(data)/1024:.1f}KB")

    if not bad:
        print("No bad covers found.")
        conn.close()
        return

    print(f"\nFixing {len(bad)} bad covers...")

    async def fix_one(title_id, code, cover_url):
        b64 = await download_cover_b64(cover_url, code)
        if b64:
            conn.execute(
                """
                INSERT INTO title_covers (title_id, cover_b64) VALUES (%s, %s)
                ON CONFLICT (title_id) DO UPDATE SET
                    cover_b64 = EXCLUDED.cover_b64, updated_at = now()
                """,
                (title_id, b64),
            )
            dims = db._cover_dims_from_b64(b64)
            if dims:
                conn.execute(
                    "UPDATE titles SET cover_w = %s, cover_h = %s, updated_at = now() WHERE id = %s",
                    (dims[0], dims[1], title_id),
                )
            print(f"FIXED: {code}")
        else:
            print(f"FAILED: {code}")

    async def run_all():
        for title_id, code, cover_url in bad:
            await fix_one(title_id, code, cover_url)
            await asyncio.sleep(0.5)

    asyncio.run(run_all())
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
