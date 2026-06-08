#!/usr/bin/env python3
"""scripts/fill_all_covers.py — 全量补充缺失封面

遍历 DuckDB 中所有 titles，为缺少 cover_b64 的作品下载封面。
复用 scrapers/v2/cover_utils.py 中的下载逻辑。
"""

from __future__ import annotations

import asyncio
import sys

import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import db
from scrapers.v2.cover_utils import download_covers_batch


async def main() -> None:
    db.init_schema()
    conn = db._conn()

    # 查询所有缺少封面的作品
    rows = conn.execute("""
        SELECT t.code, t.cover_url
        FROM titles t
        WHERE t.cover_b64 IS NULL OR t.cover_b64 = ''
        ORDER BY t.release_date_sort DESC NULLS LAST
    """).fetchall()
    conn.close()

    total = len(rows)
    if total == 0:
        print("所有作品已有封面，无需补充")
        return

    print(f"共 {total} 部作品缺少封面，开始批量下载...")

    # 批量下载封面
    cover_items = [(code, url or "") for code, url in rows]
    cover_map = await download_covers_batch(cover_items, concurrency=8)

    print(f"成功下载 {len(cover_map)} 张封面")

    # 更新数据库
    updated = 0
    conn = db._conn()
    for code, b64 in cover_map.items():
        conn.execute("""
            UPDATE titles SET cover_b64 = ?, updated_at = CURRENT_TIMESTAMP
            WHERE code = ?
        """, (b64, code))
        updated += 1
    conn.commit()
    conn.close()

    print(f"数据库更新完成：{updated} 条记录")


if __name__ == "__main__":
    asyncio.run(main())
