#!/usr/bin/env python3
"""scripts/fill_all_covers.py — 全量补充缺失封面

遍历数据库中所有 titles，为缺少封面（title_covers 无记录）的作品下载封面。
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

    # 查询所有缺少封面的作品（title_covers 无 blob）
    rows = conn.execute("""
        SELECT t.id, t.code, t.cover_url
        FROM titles t
        LEFT JOIN title_covers c ON c.title_id = t.id
        WHERE c.cover_b64 IS NULL OR c.cover_b64 = ''
        ORDER BY t.release_date_sort DESC NULLS LAST
    """).fetchall()
    conn.close()

    total = len(rows)
    if total == 0:
        print("所有作品已有封面，无需补充")
        return

    print(f"共 {total} 部作品缺少封面，开始批量下载...")

    # 批量下载封面
    cover_items = [(code, url or "") for _, code, url in rows]
    cover_map = await download_covers_batch(cover_items, concurrency=8)

    print(f"成功下载 {len(cover_map)} 张封面")

    # 更新数据库：title_covers upsert + titles.cover_w/h
    id_by_code = {code: title_id for title_id, code, _ in rows}
    updated = 0
    conn = db._conn()
    try:
        for code, b64 in cover_map.items():
            title_id = id_by_code.get(code)
            if title_id is None:
                continue
            dims = db._cover_dims_from_b64(b64)
            conn.execute(
                """
                INSERT INTO title_covers (title_id, cover_b64) VALUES (%s, %s)
                ON CONFLICT (title_id) DO UPDATE SET
                    cover_b64 = EXCLUDED.cover_b64, updated_at = now()
                """,
                (title_id, b64),
            )
            if dims:
                conn.execute(
                    "UPDATE titles SET cover_w = %s, cover_h = %s, updated_at = now() WHERE id = %s",
                    (dims[0], dims[1], title_id),
                )
            updated += 1
    finally:
        conn.close()

    print(f"数据库更新完成：{updated} 条记录")


if __name__ == "__main__":
    asyncio.run(main())
