"""Batch fix missing cover images (concurrent version).

Root cause: extractor used wrong selector div[data-link],
should be a[data-link], causing cover_url to always be None.

Usage:
    cd /root/claw-stream
    PYTHONPATH=/root/claw-stream uv run python scripts/fix_missing_covers.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from core.db import _conn
from scrapers.v2.fetchers import HttpxFetcher
from scrapers.v2.extractors import IJavTorrentExtractor
from scrapers.v2.cover_utils import download_cover_b64

CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
CONCURRENCY = 8


async def _download_one(fetcher: HttpxFetcher, title_id: int, code: str, cover_url: str) -> tuple[int, str] | None:
    """下载单个封面，返回 (title_id, b64) 或 None。"""
    try:
        b64 = await download_cover_b64(cover_url, code)
        if b64:
            return (title_id, b64)
    except Exception:
        pass
    return None


async def main() -> None:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Phase 1: collect everything needed from the DB, then close the
    # connection. Holding a DuckDB connection open across async network I/O
    # OOM-killed this script on a 4 GB host (DuckDB Allocation failure).
    conn = _conn()
    try:
        stars_to_fix = []
        for star_cfg in config.get("stars", []):
            name = star_cfg.get("name", "?")
            code = star_cfg.get("code", "?")
            url = star_cfg.get("star_page_url", "")

            title_rows = conn.execute(
                """
                SELECT t.id, t.code FROM titles t
                JOIN stars s ON s.id = t.star_id
                WHERE s.code = ? AND (t.cover_b64 IS NULL OR t.cover_b64 = '')
                """,
                (code,),
            ).fetchall()
            if title_rows and url:
                stars_to_fix.append((name, code, url, title_rows))
    finally:
        conn.close()

    print(f"Stars to fix: {len(stars_to_fix)} ({sum(len(s[3]) for s in stars_to_fix)} missing covers)")

    total_fixed = 0
    total_failed = 0

    # Phase 2: network I/O with no DB connection held.
    pending_writes: list[tuple[int, str]] = []
    async with HttpxFetcher() as fetcher:
        for name, code, url, title_rows in stars_to_fix:
            print(f"\n{name} ({code}): {len(title_rows)} missing covers")

            try:
                html = await fetcher.fetch(url)
            except Exception as exc:
                print(f"  fetch failed: {exc}")
                total_failed += len(title_rows)
                continue

            items = IJavTorrentExtractor().extract(html)
            if not items:
                print("  no items extracted")
                total_failed += len(title_rows)
                continue

            cover_map = {it.code: it.cover_url for it in items if it.cover_url}

            # 并发下载封面
            tasks = []
            for title_id, title_code in title_rows:
                cover_url = cover_map.get(title_code)
                if cover_url:
                    tasks.append(_download_one(fetcher, title_id, title_code, cover_url))
                else:
                    total_failed += 1

            semaphore = asyncio.Semaphore(CONCURRENCY)

            async def _sem_task(task):
                async with semaphore:
                    return await task

            results = await asyncio.gather(*[_sem_task(t) for t in tasks], return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    total_failed += 1
                    continue
                pending_writes.append(result)

    # Phase 3: serial DB writes on a fresh connection.
    conn = _conn()
    try:
        for title_id, b64 in pending_writes:
            try:
                conn.execute(
                    "UPDATE titles SET cover_b64 = ? WHERE id = ?",
                    (b64, title_id),
                )
                conn.commit()
                total_fixed += 1
            except Exception as exc:
                print(f"  db write error: {exc}")
                total_failed += 1
    finally:
        conn.close()

    print(f"\nDone: {total_fixed} fixed, {total_failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
