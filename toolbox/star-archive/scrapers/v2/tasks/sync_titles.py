"""scrapers/v2/tasks/sync_titles.py — 同步女优最新作品

核心变更：不再固定只取前 3 个，而是同步所有数据库中不存在的作品 +
更新已有作品的元数据，确保新作品永远不会被遗漏。
"""

from __future__ import annotations

import asyncio
import json
import random

from core import get_logger
from core import db
from scrapers.v2.fetchers import PlaywrightFetcher
from scrapers.v2.extractors import IJavTorrentExtractor
from scrapers.v2.sinks import TitleSyncSink
from scrapers.v2.schemas import VideoItem, StarConfig

log = get_logger("sync-titles")

MAX_NEW_TITLES = 20  # 单次同步最多处理的新作品数（防止作品过多的 star 拖垮同步）


def _sort_key(item: VideoItem) -> str:
    """按日期降序排序的 key。日期格式为 dd/mm/YYYY。"""
    if item.release_date:
        parts = item.release_date.split("/")
        if len(parts) == 3:
            # parts[0]=dd, parts[1]=mm, parts[2]=YYYY → YYYYMMDD
            return parts[2] + parts[1].zfill(2) + parts[0].zfill(2)
    return "00000000"


def _dedup(items: list[VideoItem]) -> list[VideoItem]:
    """按 code 去重，按日期降序排序"""
    seen: set[str] = set()
    unique: list[VideoItem] = []
    for it in items:
        if it.code not in seen:
            seen.add(it.code)
            unique.append(it)
    unique.sort(key=_sort_key, reverse=True)
    return unique


async def sync_star(
    fetcher: PlaywrightFetcher,
    star: StarConfig,
    semaphore: asyncio.Semaphore,
) -> dict:
    """同步单个 star 的作品：增量抓取新作品 + 更新已有作品元数据。"""
    name = star.name
    code = star.code
    handle = star.handle
    star_page_url = star.star_page_url

    star_id = db.upsert_star(name=name, handle=handle, code=code)

    if not star_page_url:
        log.warning(f"no star_page_url for {name}, skipping")
        return {"name": name, "titles": [], "count": 0}

    async with semaphore:
        try:
            html = await fetcher.fetch(star_page_url, delay_ms=random.randint(500, 1500))
        except Exception as e:
            log.error(f"fetch failed: {name}: {type(e).__name__}: {e}")
            return {"name": name, "titles": [], "count": 0}

    extractor = IJavTorrentExtractor()
    raw_items = extractor.extract(html)
    log.info(f"{name}: {len(raw_items)} total items on page")

    items = _dedup(raw_items)
    log.info(f"{name}: {len(items)} items after dedup")

    # 分类：新作品 vs 已有作品
    new_items: list[VideoItem] = []
    existing_items: list[VideoItem] = []
    for it in items:
        if db.title_exists(star_id, it.code):
            existing_items.append(it)
        else:
            new_items.append(it)

    # 限制新作品数量，防止 star 作品过多时同步超时
    if len(new_items) > MAX_NEW_TITLES:
        log.info(f"{name}: {len(new_items)} new items, limiting to {MAX_NEW_TITLES}")
        new_items = new_items[:MAX_NEW_TITLES]

    # 要同步的总列表：新作品 + 已有作品（用于元数据更新）
    # 已有作品只更新前 10 个，避免大量无意义的 UPDATE
    to_sync = new_items + existing_items[:10]

    sink = TitleSyncSink(star_id=star_id, name=name)
    for it in to_sync:
        await sink.write(it)

    log.info(f"done: {name}: {len(new_items)} new + {len(existing_items[:5])} updated")
    return {"name": name, "titles": to_sync, "count": len(new_items)}


async def run(config_path: str = "config.json", concurrency: int = 4) -> list[dict]:
    """主入口：读取配置，并发同步所有 stars"""
    db.init_schema()

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    stars = [StarConfig(**s) for s in raw.get("stars", [])]
    log.info(f"fetching titles from {len(stars)} star pages...")

    async with PlaywrightFetcher() as fetcher:
        sem = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *[sync_star(fetcher, star, sem) for star in stars],
            return_exceptions=True,
        )

    # 统计（只读查询，无需排队）
    conn = db._conn()
    rows = conn.execute("""
        SELECT s.code, s.name, COUNT(t.id) as title_count
        FROM stars s
        LEFT JOIN titles t ON t.star_id = s.id
        GROUP BY s.id, s.code, s.name
        ORDER BY s.name
    """).fetchall()
    conn.close()

    total = 0
    for _, name, count in rows:
        log.info(f"{name}: {count} titles")
        total += count
    log.info(f"done, total {total} titles")

    # 过滤掉异常结果
    clean: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            log.error(f"sync star exception: {r}")
        else:
            clean.append(r)
    return clean


if __name__ == "__main__":
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    asyncio.run(run(config))
