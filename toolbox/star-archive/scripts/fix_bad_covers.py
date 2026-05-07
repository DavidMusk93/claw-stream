#!/usr/bin/env python3
"""修复数据库中尺寸过小的封面图。

用法: cd toolbox/star-archive && python3 scripts/fix_bad_covers.py
"""
import sys, os, base64, struct, asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import duckdb
from scrapers.search_news import download_cover_b64, _parse_image_size

def main():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'claw.duckdb')
    conn = duckdb.connect(db_path)

    # 找出所有 bad covers
    rows = conn.execute("""
        SELECT id, code, cover_b64, cover_url
        FROM titles
        WHERE cover_b64 IS NOT NULL
    """).fetchall()

    bad = []
    for title_id, code, b64, cover_url in rows:
        if b64.startswith('data:image/'):
            b64 = b64.split(',', 1)[1]
        data = base64.b64decode(b64)
        w, h = _parse_image_size(data)
        if len(data) < 15 * 1024 or w < 200 or h < 200:
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
                "UPDATE titles SET cover_b64 = ? WHERE id = ?",
                (b64, title_id)
            )
            print(f"FIXED: {code}")
        else:
            print(f"FAILED: {code}")

    async def run_all():
        for title_id, code, cover_url in bad:
            await fix_one(title_id, code, cover_url)
            await asyncio.sleep(0.5)

    asyncio.run(run_all())
    conn.commit()
    conn.close()
    print("Done.")

if __name__ == "__main__":
    main()
