#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "duckdb"]
# ///
"""fetch-social.py — 抓取女演员 X(Twitter) 最新动态，写入 DuckDB

用法: uv run fetch-social.py <config.json>
"""

import sys, json, os, asyncio
from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logger import get_logger
import db

log = get_logger("fetch-social")

NITTER_MIRRORS = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.space",
    "https://xcancel.com",
]


async def fetch_x_posts(handle: str) -> list[dict]:
    """从 nitter 镜像抓取最近推文，返回 [{content, url, posted_at}]"""
    posts = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = await browser.new_page()

        for base in NITTER_MIRRORS:
            url = f"{base}/{handle}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2500)

                # nitter / xcancel 的推文选择器
                tweets = await page.eval_on_selector_all(
                    ".timeline .timeline-item, .main-tweet, .tweet-body",
                    """els => {
                        const out = [];
                        for (const el of els.slice(0, 3)) {
                            const textEl = el.querySelector('.tweet-content, .tweet-text, [data-testid="tweetText"]');
                            const timeEl = el.querySelector('.tweet-date a, time, .time a');
                            const text = textEl ? textEl.textContent.trim() : '';
                            const href = timeEl ? timeEl.getAttribute('href') : '';
                            const timeStr = timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent.trim()) : '';
                            if (text && text.length > 5) {
                                out.push({content: text, url: href, posted_at: timeStr});
                            }
                        }
                        return out;
                    }"""
                )
                if tweets and len(tweets) > 0:
                    posts = tweets
                    log.info(f"{handle}: {len(posts)} posts from {base}")
                    break
            except Exception as e:
                log.debug(f"{handle} {base} failed: {e}")
                continue

        await browser.close()

    if not posts:
        log.warning(f"{handle}: no posts found")
    return posts


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(config_path) as f:
        config = json.load(f)

    db.init_schema()

    # 建立 name -> actress_id 映射
    conn = db._conn()
    name_to_id = {}
    for row in conn.execute("SELECT id, name FROM actresses").fetchall():
        name_to_id[row[1]] = row[0]
    conn.close()

    total = 0
    for a in config.get("actresses", []):
        name = a.get("name")
        handle = a.get("handle")
        if not handle:
            log.info(f"{name}: no handle, skip")
            continue
        if name not in name_to_id:
            log.warning(f"{name}: not in DB, skip")
            continue

        posts = await fetch_x_posts(handle)
        actress_id = name_to_id[name]
        for p in posts:
            db.upsert_social_post(
                actress_id=actress_id,
                platform="x",
                content=p["content"],
                post_url=p["url"] if p["url"].startswith("http") else f"https://x.com{p['url']}",
                posted_at=p["posted_at"] if p["posted_at"] else None,
            )
            total += 1

    log.info(f"fetch-social done, total {total} posts")


if __name__ == "__main__":
    asyncio.run(main())
