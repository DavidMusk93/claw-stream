#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "beautifulsoup4"]
# ///
"""browser-fetch.py — 用 Playwright 抓取网页（替代 curl 抓不动的场景）
支持 JavaScript 渲染、Cloudflare 规避。

用法: uv run browser-fetch.py <url>
"""

import sys, asyncio
from playwright.async_api import async_playwright

async def fetch(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            content = await page.content()
            title = await page.title()
            print(f"Title: {title}", file=sys.stderr)
            return content
        finally:
            await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run browser-fetch.py <url>", file=sys.stderr)
        sys.exit(1)
    result = asyncio.run(fetch(sys.argv[1]))
    print(result[:5000])  # truncate for quick view
    print(f"\n--- {len(result)} chars total ---", file=sys.stderr)
