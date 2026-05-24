"""scrapers/v2/tasks/sync_social.py — 同步女演员 X(Twitter) 最新动态

对应原 fetch_social.py。
"""

from __future__ import annotations

import asyncio
import json

from core import get_logger
from core import db
from scrapers.v2.fetchers import PlaywrightFetcher
from scrapers.v2.extractors import NitterExtractor
from scrapers.v2.sinks import SocialSyncSink
from scrapers.v2.schemas import StarConfig

log = get_logger("sync-social")

NITTER_MIRRORS = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.space",
    "https://xcancel.com",
]


async def fetch_x_posts(fetcher: PlaywrightFetcher, handle: str) -> list:
    """从 nitter 镜像抓取最近推文"""
    extractor = NitterExtractor()
    for base in NITTER_MIRRORS:
        url = f"{base}/{handle}"
        try:
            html = await fetcher.fetch(url, delay_ms=2500, timeout=15000)
            posts = extractor.extract(html)
            if posts:
                log.info(f"{handle}: {len(posts)} posts from {base}")
                return posts
        except Exception as e:
            log.debug(f"{handle} {base} failed: {e}")
            continue
    log.warning(f"{handle}: no posts found")
    return []


async def sync_star(
    fetcher: PlaywrightFetcher,
    star: StarConfig,
    name_to_id: dict[str, int],
) -> int:
    """同步单个 star 的社交动态，返回写入条数"""
    name = star.name
    handle = star.handle
    if not handle:
        log.info(f"{name}: no handle, skip")
        return 0
    if name not in name_to_id:
        log.warning(f"{name}: not in DB, skip")
        return 0

    posts = await fetch_x_posts(fetcher, handle)
    star_id = name_to_id[name]
    sink = SocialSyncSink(star_id=star_id)
    for post in posts:
        await sink.write(post)
    return len(posts)


async def run(config_path: str = "config.json") -> int:
    """主入口"""
    db.init_schema()

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    stars = [StarConfig(**s) for s in raw.get("stars", [])]

    conn = db._conn()
    name_to_id = {}
    for row in conn.execute("SELECT id, name FROM stars").fetchall():
        name_to_id[row[1]] = row[0]
    conn.close()

    total = 0
    async with PlaywrightFetcher() as fetcher:
        results = await asyncio.gather(
            *[sync_star(fetcher, star, name_to_id) for star in stars],
            return_exceptions=True,
        )

    for r in results:
        if isinstance(r, int):
            total += r
        elif isinstance(r, Exception):
            log.error(f"sync social exception: {r}")

    log.info(f"fetch-social done, total {total} posts")
    return total


if __name__ == "__main__":
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    asyncio.run(run(config))
