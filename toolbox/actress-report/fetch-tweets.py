#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["browser-use", "playwright"]
# ///
"""fetch-tweets.py — 用 browser-use 获取 X 最近的动态"""

import asyncio, json, sys, os
from browser_use import Agent, Browser, BrowserConfig
from browser_use import ChatOpenAI

# For local open-source LLM, use ChatOpenAI with a local endpoint
# or use ChatBrowserUse() for the built-in chat

OUTDIR = "/tmp/actress-tweets"
os.makedirs(OUTDIR, exist_ok=True)

async def fetch_tweets(handle: str) -> list[str]:
    out = os.path.join(OUTDIR, f"{handle}.json")
    if os.path.exists(out):
        print(f"  ✅ {handle} (cached)")
        return json.load(open(out))

    browser = Browser(config=BrowserConfig(headless=True))

    try:
        agent = Agent(
            task=f"""Go to https://x.com/{handle} and get the text of their 2 most recent tweets.
Return ONLY the tweet texts as a JSON array of strings like ["tweet1", "tweet2"].
If no tweets found, return []. Do not include any other text.""",
            browser=browser,
            llm=ChatOpenAI(model="deepseek-chat"),  # will use OPENAI_API_KEY if set
        )

        result = await agent.run(max_steps=15)

        # Parse the result
        try:
            tweets = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            tweets = []

        json.dump(tweets, open(out, "w"))
        print(f"  ✅ {handle} ({len(tweets)} tweets)")
        return tweets

    finally:
        await browser.close()

async def main():
    handles = sys.argv[1:]
    if not handles:
        print("Usage: uv run fetch-tweets.py <handle1> [handle2 ...]")
        return

    print("[tweets] 获取最近的动态...")
    tasks = [fetch_tweets(h) for h in handles]
    await asyncio.gather(*tasks)
    print("[tweets] 完成")

if __name__ == "__main__":
    asyncio.run(main())
