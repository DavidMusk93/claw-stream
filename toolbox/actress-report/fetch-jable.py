#!/usr/bin/env python3
"""fetch-jable.py — 按番号从 jable.tv 获取 m3u8 和高清封面

用法: python3 fetch-jable.py <config.json>

数据流：
  1. 从 DuckDB 查询缺少 jable 数据的作品
  2. 对每个番号访问 https://en.jable.tv/videos/<code>/
  3. 提取 m3u8_url + 高清封面(preview.jpg)
  4. 更新 DuckDB，同时缓存封面和 m3u8 片段到本地
"""

import sys, json, os, re, asyncio
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logger import get_logger
import db

log = get_logger("fetch-jable")

OUTDIR = "/tmp/star-jable"
COVERS_DIR = os.path.join(OUTDIR, "covers")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

# 视频片段缓存配置
CACHE_SEGMENTS = os.environ.get("CACHE_SEGMENTS", "1") == "1"
MAX_SEGMENTS = int(os.environ.get("MAX_SEGMENTS", "12"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def detect_ext(data: bytes) -> str:
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and b"WEBP" in data[:12]:
        return ".webp"
    return ".jpg"


async def download_cover(client: httpx.AsyncClient, url: str, out_path: str) -> str:
    try:
        resp = await client.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 3000:
            ext = detect_ext(resp.content)
            final_path = out_path.replace(".jpg", ext) if out_path.endswith(".jpg") else out_path + ext
            with open(final_path, "wb") as f:
                f.write(resp.content)
            return final_path
    except Exception:
        pass
    return ""


def resolve_url(base: str, rel: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, rel.strip())


async def cache_m3u8_segments(client: httpx.AsyncClient, m3u8_url: str, code: str) -> str:
    """下载 m3u8 和前 N 个片段，返回本地 m3u8 路径"""
    if not CACHE_SEGMENTS:
        return ""
    try:
        resp = await client.get(m3u8_url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return ""
        m3u8_content = resp.text

        base_url = m3u8_url.rsplit("/", 1)[0] + "/"
        lines = m3u8_content.splitlines()

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
                kresp = await client.get(key_url, headers=HEADERS, timeout=15, follow_redirects=True)
                if kresp.status_code == 200:
                    with open(os.path.join(cache_subdir, "key.key"), "wb") as f:
                        f.write(kresp.content)
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
                sresp = await client.get(seg_url, headers=HEADERS, timeout=30, follow_redirects=True)
                if sresp.status_code == 200 and len(sresp.content) > 1000:
                    with open(seg_path, "wb") as f:
                        f.write(sresp.content)
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


async def fetch_video_meta(client: httpx.AsyncClient, code: str) -> dict:
    """访问 jable 视频页获取 m3u8 和封面"""
    try:
        url = f"https://en.jable.tv/videos/{code.lower()}/"
        resp = await client.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        html = resp.text
        result = {"m3u8_url": "", "cover_url": ""}
        m3u8_list = re.findall(r'https://[^"\'\s]+\.m3u8', html)
        if m3u8_list:
            result["m3u8_url"] = m3u8_list[0]
        m = re.search(r'<meta property="og:image" content="(https://assets-cdn\.jable\.tv/[^"]+)"', html)
        if m:
            result["cover_url"] = m.group(1)
        else:
            m = re.search(r'poster="(https://assets-cdn\.jable\.tv/[^"]+)"', html)
            if m:
                result["cover_url"] = m.group(1)
        return result
    except Exception:
        pass
    return {"m3u8_url": "", "cover_url": ""}


async def fetch_star(name: str, code: str, titles: list):
    """为指定 star 的待补充 titles 抓取 jable 数据"""
    os.makedirs(OUTDIR, exist_ok=True)
    os.makedirs(os.path.join(COVERS_DIR, code.lower()), exist_ok=True)

    if not titles:
        return

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        sem = asyncio.Semaphore(3)

        async def fetch_one(title: dict):
            title_id = title["id"]
            c = title["code"].upper()
            async with sem:
                meta = await fetch_video_meta(client, c)
                if meta["m3u8_url"] or meta["cover_url"]:
                    db.update_jable(title_id, meta["m3u8_url"], meta["cover_url"])
                if meta["cover_url"]:
                    out = os.path.join(COVERS_DIR, code.lower(), f"{c.lower()}.jpg")
                    await download_cover(client, meta["cover_url"], out)
                if meta["m3u8_url"]:
                    log.info(f"m3u8 ok: {c}")
                    await cache_m3u8_segments(client, meta["m3u8_url"], c)
                return {"code": c, **meta}

        results = await asyncio.gather(*[fetch_one(t) for t in titles])
        successful = [r for r in results if r.get("m3u8_url") or r.get("cover_url")]

    log.info(f"done: {name}: {len(successful)}/{len(titles)} titles with jable data")


async def main():
    db.init_schema()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(config_file) as f:
        config = json.load(f)

    stars = [a for a in config.get("stars", []) if not a.get("type") or a.get("type") == "solo"]

    # 从 DuckDB 获取缺少 jable 数据的作品
    titles_without_jable = db.get_titles_without_jable()
    star_titles_map: dict[str, list] = {}
    for title_id, code, title, star_name in titles_without_jable:
        star_titles_map.setdefault(star_name, []).append({"id": title_id, "code": code, "title": title})

    log.info("start fetching...")
    for a in stars:
        name = a["name"]
        code = a["code"]
        titles = star_titles_map.get(name, [])
        if not titles:
            log.info(f"skipping {name}: all titles have jable data")
            continue
        log.info(f"fetching: {name} ({len(titles)} titles)...")
        await fetch_star(name, code, titles)
        await asyncio.sleep(0.5)
    log.info("done")


if __name__ == "__main__":
    asyncio.run(main())
