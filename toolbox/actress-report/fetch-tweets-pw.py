#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright"]
# ///
"""fetch-tweets-pw.py — 用 Playwright 抓 X 最近的动态（绕过 Cloudflare）

用法: uv run fetch-tweets-pw.py <handle1> [handle2 ...]
"""

import sys, json, os, asyncio
from playwright.async_api import async_playwright

OUTDIR = "/tmp/actress-tweets"

async def fetch_tweets(handle: str) -> list[str]:
    os.makedirs(OUTDIR, exist_ok=True)
    outfile = os.path.join(OUTDIR, f"{handle}.json")

    if os.path.exists(outfile):
        with open(outfile) as f:
            data = json.load(f)
        print(f"  ✅ {handle} (cached, {len(data)} tweets)")
        return data

    tweets = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = await browser.new_page()

        for url in [f"https://nitter.net/{handle}", f"https://xcancel.com/{handle}"]:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                # Wait a moment for dynamic content
                await page.wait_for_timeout(2000)
                # Extract tweet text
                tweets = await page.eval_on_selector_all(
                    ".tweet-content, [data-testid='tweetText']",
                    "els => els.slice(0,2).map(e => e.textContent.trim())"
                )
                if tweets:
                    break
            except Exception:
                continue
        await browser.close()

    json.dump(tweets, open(outfile, "w"))
    print(f"  {'✅' if tweets else '⚠️'} {handle} ({len(tweets)} tweets)")
    return tweets

async def main():
    handles = sys.argv[1:]
    if not handles:
        print("Usage: uv run fetch-tweets-pw.py <handle1> [handle2 ...]")
        return
    print("[tweets] 获取最近的动态...")
    results = await asyncio.gather(*[fetch_tweets(h) for h in handles])
    total = sum(len(r) for r in results)
    print(f"[tweets] 完成，共 {total} 条推文")

if __name__ == "__main__":
    asyncio.run(main())
