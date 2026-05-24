"""scrapers/v2/tasks/sync_jable.py — 按番号从 jable.tv 获取 m3u8 和高清封面

对应原 fetch_jable.py。
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from core import get_logger
from core import db
from scrapers.v2.fetchers import HttpxFetcher
from scrapers.v2.extractors import JableExtractor
from scrapers.v2.cover_utils import is_good_cover

log = get_logger("sync-jable")

OUTDIR = "/tmp/star-jable"
COVERS_DIR = os.path.join(OUTDIR, "covers")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
CACHE_SEGMENTS = os.environ.get("CACHE_SEGMENTS", "1") == "1"
MAX_SEGMENTS = int(os.environ.get("MAX_SEGMENTS", "12"))


def detect_ext(data: bytes) -> str:
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and b"WEBP" in data[:12]:
        return ".webp"
    return ".jpg"


async def download_cover(client: HttpxFetcher, url: str, out_path: str) -> str:
    try:
        data = await client.fetch_bytes(url)
        if len(data) > 3000:
            ext = detect_ext(data)
            final_path = out_path.replace(".jpg", ext) if out_path.endswith(".jpg") else out_path + ext
            with open(final_path, "wb") as f:
                f.write(data)
            return final_path
    except Exception:
        pass
    return ""


def resolve_url(base: str, rel: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, rel.strip())


async def cache_m3u8_segments(client: HttpxFetcher, m3u8_url: str, code: str) -> str:
    """下载 m3u8 和前 N 个片段，返回本地 m3u8 路径"""
    if not CACHE_SEGMENTS:
        return ""
    try:
        m3u8_text = await client.fetch(m3u8_url)
        base_url = m3u8_url.rsplit("/", 1)[0] + "/"
        lines = m3u8_text.splitlines()

        segments = []
        key_url = None
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("#EXT-X-KEY:"):
                km = re.search(r'URI="([^"]+)"', line)
                if km:
                    key_url = resolve_url(base_url, km.group(1))
            elif line.startswith("#EXTINF:") and i + 1 < len(lines):
                seg_url = resolve_url(base_url, lines[i + 1])
                segments.append((seg_url, lines[i + 1].strip()))
                i += 1
            i += 1

        if not segments:
            return ""

        cache_subdir = os.path.join(CACHE_DIR, code.lower())
        os.makedirs(cache_subdir, exist_ok=True)

        if key_url:
            try:
                key_data = await client.fetch_bytes(key_url)
                with open(os.path.join(cache_subdir, "key.key"), "wb") as f:
                    f.write(key_data)
            except Exception:
                pass

        seg_limit = min(MAX_SEGMENTS, len(segments))
        downloaded = 0
        for idx, (seg_url, seg_name_raw) in enumerate(segments[:seg_limit]):
            seg_name = seg_name_raw.rsplit("/", 1)[-1]
            seg_path = os.path.join(cache_subdir, seg_name)
            if os.path.exists(seg_path) and os.path.getsize(seg_path) > 1000:
                downloaded += 1
                continue
            try:
                seg_data = await client.fetch_bytes(seg_url)
                if len(seg_data) > 1000:
                    with open(seg_path, "wb") as f:
                        f.write(seg_data)
                    downloaded += 1
            except Exception:
                pass

        if downloaded == 0:
            return ""

        output_lines = []
        kept = 0
        for line in lines:
            if line.startswith("#EXT-X-KEY:") and key_url:
                output_lines.append(re.sub(r'URI="[^"]+"', 'URI="key.key"', line))
            elif line.startswith("#EXTINF:"):
                if kept < seg_limit:
                    output_lines.append(line)
                kept += 1
            elif not line.startswith("#") and line.strip():
                if kept <= seg_limit:
                    seg_name = line.strip().rsplit("/", 1)[-1]
                    output_lines.append(seg_name)
            elif line.startswith("#EXT-X-ENDLIST"):
                break
            else:
                output_lines.append(line)
        output_lines.append("#EXT-X-ENDLIST")

        local_m3u8 = os.path.join(cache_subdir, f"{code.lower()}.m3u8")
        with open(local_m3u8, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines) + "\n")
        log.info(f"cache ok: {code}: {downloaded}/{seg_limit} segments")
        return local_m3u8
    except Exception as e:
        log.warning(f"cache warn: {code}: {e}")
    return ""


async def sync_jable_for_titles(titles: list[dict]) -> list[dict]:
    """为一批 title 抓取 jable 数据"""
    results: list[dict] = []
    extractor = JableExtractor()

    async with HttpxFetcher() as client:
        sem = asyncio.Semaphore(3)

        async def fetch_one(title: dict) -> dict:
            title_id = title["id"]
            code = title["code"].upper()
            async with sem:
                try:
                    html = await client.fetch(f"https://en.jable.tv/videos/{code.lower()}/")
                except Exception:
                    return {"code": code, "m3u8_url": "", "cover_url": ""}

                meta = extractor.extract(html, code=code)
                if meta.m3u8_url or meta.cover_url:
                    db.update_jable(title_id, meta.m3u8_url, meta.cover_url)
                if meta.cover_url:
                    out = os.path.join(COVERS_DIR, code.lower(), f"{code.lower()}.jpg")
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    await download_cover(client, meta.cover_url, out)
                if meta.m3u8_url:
                    log.info(f"m3u8 ok: {code}")
                    await cache_m3u8_segments(client, meta.m3u8_url, code)
                return {"code": code, "m3u8_url": meta.m3u8_url, "cover_url": meta.cover_url}

        results = await asyncio.gather(*[fetch_one(t) for t in titles])
    return results


async def run(config_path: str = "config.json") -> None:
    """主入口"""
    db.init_schema()

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    stars = [s for s in raw.get("stars", []) if not s.get("type") or s.get("type") == "solo"]

    # 获取缺少 jable 数据的作品
    titles_without_jable = db.get_titles_without_jable()
    star_titles_map: dict[str, list] = {}
    for title_id, code, title, star_name in titles_without_jable:
        star_titles_map.setdefault(star_name, []).append({"id": title_id, "code": code, "title": title})

    log.info("start fetching...")
    for star in stars:
        name = star["name"]
        code = star["code"]
        titles = star_titles_map.get(name, [])
        if not titles:
            log.info(f"skipping {name}: all titles have jable data")
            continue
        log.info(f"fetching: {name} ({len(titles)} titles)...")
        results = await sync_jable_for_titles(titles)
        successful = [r for r in results if r.get("m3u8_url") or r.get("cover_url")]
        log.info(f"done: {name}: {len(successful)}/{len(titles)} titles with jable data")
        await asyncio.sleep(0.5)
    log.info("done")


if __name__ == "__main__":
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    asyncio.run(run(config))
