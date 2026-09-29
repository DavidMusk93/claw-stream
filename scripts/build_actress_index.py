"""scripts/build_actress_index.py — Build the local ijavtorrent actress index.

ijavtorrent has no actress search endpoint (its /actresses directory ignores
query params and spans ~900 paginated pages), so /api/search cannot resolve a
name like "凪ひかる" to a followable actress page URL. This script crawls the
directory once and writes data/actress_index.json:

    {"built_at": "...", "actresses": [{"name", "url", "image"}, ...]}

The search router loads the file lazily (mtime-cached) for name matching.
Re-run periodically (weekly-ish) to pick up new actresses; the crawl is
idempotent and safe to interrupt (writes atomically at the end).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

BASE = "https://ijavtorrent.com"
OUT = Path(__file__).resolve().parent.parent / "data" / "actress_index.json"
CONCURRENCY = 4

# Two anchors per card share the actress URL: the first wraps the photo (or a
# name-placeholder div for imageless actresses — most of the directory), the
# second carries the text "Name (N movies)". Extract independently, merge.
_IMG_RE = re.compile(
    r'<a href="(/actress/[a-z0-9-]+-\d+/?)">\s*<img src="([^"]+)"', re.IGNORECASE
)
_NAME_RE = re.compile(
    r'<a href="(/actress/[a-z0-9-]+-\d+/?)">([^<]+?)\s*\((\d+) movies?\)</a>',
    re.IGNORECASE,
)
_LAST_PAGE_RE = re.compile(r'/actresses\?page=(\d+)"')


def _parse_page(html: str) -> list[dict]:
    images = {path: img for path, img in _IMG_RE.findall(html)}
    return [
        {
            "name": name.strip(),
            # Strip the trailing slash: POST /api/stars/add anchors its
            # actress-URL regex at the numeric id ($).
            "url": f"{BASE}{path.rstrip('/')}",
            "image": images.get(path, ""),
            "movies": int(movies),
        }
        for path, name, movies in _NAME_RE.findall(html)
        if name.strip()
    ]


def _last_page(html: str) -> int:
    pages = [int(p) for p in _LAST_PAGE_RE.findall(html)]
    return max(pages) if pages else 1


async def main() -> None:
    t0 = time.time()
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0"}, timeout=20, follow_redirects=True
    ) as client:
        r = await client.get(f"{BASE}/actresses")
        r.raise_for_status()
        total = _last_page(r.text)
        print(f"directory has {total} pages", flush=True)

        actresses: dict[str, dict] = {a["url"]: a for a in _parse_page(r.text)}
        sem = asyncio.Semaphore(CONCURRENCY)

        async def fetch_page(page: int) -> None:
            async with sem:
                for attempt in range(3):
                    try:
                        resp = await client.get(f"{BASE}/actresses?page={page}")
                        resp.raise_for_status()
                        for a in _parse_page(resp.text):
                            actresses.setdefault(a["url"], a)
                        return
                    except Exception as exc:
                        if attempt == 2:
                            print(f"page {page}: FAILED {exc}", flush=True)
                        else:
                            await asyncio.sleep(2 * (attempt + 1))

        done = 0
        for chunk_start in range(2, total + 1, 50):
            chunk = range(chunk_start, min(chunk_start + 50, total + 1))
            await asyncio.gather(*(fetch_page(p) for p in chunk))
            done += len(list(chunk))
            print(f"{done}/{total - 1} pages, {len(actresses)} actresses", flush=True)

    payload = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "actresses": sorted(actresses.values(), key=lambda a: a["name"].lower()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    tmp.replace(OUT)
    print(f"wrote {len(actresses)} actresses → {OUT} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
