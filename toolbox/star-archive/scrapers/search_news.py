#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "httpx", "duckdb"]
# ///
"""search-news.py — 从 ijavtorrent.com star 个人主页获取单体作品数据

抓取策略：直接访问star 个人主页 /actress/{slug}-{id}，页面只包含该女优的作品。
单体过滤：通过作品卡片中的 star 标签数量判断（1 个=单体，>1 个=共演/合集）。
封面获取策略：ijavtorrent CDN → DMM CDN → placeholder。

用法: uv run search-news.py <config.json>
"""

import sys, json, os, asyncio, re, random, base64, urllib.parse, html as htmlmod, struct
from playwright.async_api import async_playwright
import httpx

from core import get_logger
from core import db

log = get_logger("search-news")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
]


def _parse_image_size(data: bytes) -> tuple[int, int]:
    """解析 JPEG/PNG/WebP 图片尺寸，返回 (width, height)。"""
    if len(data) < 8:
        return (0, 0)
    # JPEG
    if data[:2] == b'\xff\xd8':
        i = 2
        while i < len(data) - 1:
            if data[i] == 0xFF:
                marker = data[i + 1]
                if marker == 0xD9:
                    break
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    if i + 9 < len(data):
                        h = struct.unpack('>H', data[i + 5:i + 7])[0]
                        w = struct.unpack('>H', data[i + 7:i + 9])[0]
                        return (w, h)
                    break
                if marker not in (0x00, 0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4,
                                   0xD5, 0xD6, 0xD7, 0xD8, 0xD9):
                    if i + 3 < len(data):
                        seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
                        i += 2 + seg_len
                        continue
            i += 1
        return (0, 0)
    # PNG
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        if len(data) >= 24:
            w = struct.unpack('>I', data[16:20])[0]
            h = struct.unpack('>I', data[20:24])[0]
            return (w, h)
        return (0, 0)
    # WebP
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        # VP8 (lossy) - search for keyframe start code in chunk data
        if data[12:16] == b'VP8 ' and len(data) >= 40:
            chunk_data = data[20:40]
            for i in range(len(chunk_data) - 6):
                if chunk_data[i:i + 3] == b'\x9d\x01\x2a':
                    w = int.from_bytes(chunk_data[i + 3:i + 5], 'little') & 0x3FFF
                    h = int.from_bytes(chunk_data[i + 5:i + 7], 'little') & 0x3FFF
                    return (w, h)
            return (0, 0)
        # VP8L (lossless)
        if data[12:16] == b'VP8L' and len(data) >= 25:
            bits = struct.unpack('<I', data[21:25])[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return (w, h)
        # VP8X (extended)
        if data[12:16] == b'VP8X' and len(data) >= 30:
            w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
            h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
            return (w, h)
        return (0, 0)
    return (0, 0)


def _is_good_cover(data: bytes) -> bool:
    """检查封面是否足够高清：>= 15KB 且尺寸 >= 200x200。"""
    if len(data) < 15 * 1024:
        return False
    w, h = _parse_image_size(data)
    return w >= 200 and h >= 200


def _parse_count(s: str) -> int | None:
    """将 '1.2K' / '3M' 等字符串转为整数"""
    if not s:
        return None
    s = s.lower().replace(",", "").strip()
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        elif s.endswith("m"):
            return int(float(s[:-1]) * 1000000)
        else:
            return int(float(s))
    except ValueError:
        return None


async def random_delay(min_s=1.0, max_s=2.5):
    await asyncio.sleep(random.uniform(min_s, max_s))


def _extract_dn(magnet_url: str) -> str:
    """从 magnet dn 参数中提取原始文件名（含 HTML entity 解码）"""
    if not magnet_url:
        return ""
    try:
        decoded_url = htmlmod.unescape(magnet_url)
        parsed = urllib.parse.urlparse(decoded_url)
        params = urllib.parse.parse_qs(parsed.query)
        dn = params.get("dn", [""])[0]
        return urllib.parse.unquote(dn) if dn else ""
    except Exception:
        return ""


def extract_resolution(magnet_url: str) -> str:
    """从 magnet dn 参数中提取清晰度（含 HTML entity 解码）"""
    decoded = _extract_dn(magnet_url)
    if not decoded:
        return ""
    for pat in ["[4K]", "[FHDC]", "[FHD]", "[HD/720p]", "[HD]", "[720p]", "[1080p]"]:
        if pat.lower() in decoded.lower():
            return pat
    return ""


async def _fetch_jable_cover(code: str) -> str:
    """从 Jable.tv 抓取封面 URL，返回 base64 或空。优先使用高清封面。"""
    if not code:
        return ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, chromium_sandbox=False,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1400, "height": 900},
                locale="en-US",
            )
            page = await ctx.new_page()
            try:
                await page.goto(
                    f"https://en.jable.tv/videos/{code.lower()}/",
                    wait_until="domcontentloaded", timeout=15000
                )
                await page.wait_for_timeout(2000)
                html = await page.content()
                m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
                if not m:
                    return ""
                jable_url = m.group(1)
                async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                    resp = await client.get(jable_url, headers={"User-Agent": random.choice(USER_AGENTS)})
                    if resp.status_code == 200 and _is_good_cover(resp.content):
                        log.info(f"cover ok: {code} (Jable, {len(resp.content)//1024}KB)")
                        return f"data:image/jpeg;base64,{base64.b64encode(resp.content).decode()}"
                    elif resp.status_code == 200:
                        log.warning(f"cover small: {code} (Jable, {len(resp.content)//1024}KB)")
            except Exception:
                pass
            finally:
                await browser.close()
    except Exception:
        pass
    return ""


async def download_cover_b64(cover_url: str, code: str = "") -> str:
    """下载封面，优先高清源：Jable.tv → DMM CDN → ijavtorrent CDN → 返回 base64 或空"""
    tried = set()

    # 1. 优先尝试 Jable.tv 高清封面
    if code:
        jable_b64 = await _fetch_jable_cover(code)
        if jable_b64:
            return jable_b64

    # 2. DMM CDN fallback
    if code:
        c = code.lower().replace("-", "")
        dmm_url = f"https://pics.dmm.co.jp/mono/movie/adult/{c}/{c}pl.jpg"
        if dmm_url not in tried:
            tried.add(dmm_url)
            try:
                async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                    resp = await client.get(dmm_url, headers={"User-Agent": random.choice(USER_AGENTS)})
                    if resp.status_code == 200 and _is_good_cover(resp.content):
                        b64 = base64.b64encode(resp.content).decode()
                        log.info(f"cover ok: {code} (DMM, {len(resp.content)//1024}KB)")
                        return f"data:image/jpeg;base64,{b64}"
                    elif resp.status_code == 200:
                        log.warning(f"cover small: {code} (DMM, {len(resp.content)//1024}KB)")
            except Exception:
                pass

    # 3. ijavtorrent CDN（可能返回缩略图，需检查尺寸）
    for url in [cover_url] if cover_url else []:
        if url in tried:
            continue
        tried.add(url)
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": random.choice(USER_AGENTS)})
                if resp.status_code == 200 and _is_good_cover(resp.content):
                    b64 = base64.b64encode(resp.content).decode()
                    log.info(f"cover ok: {code} (ijavtorrent, {len(resp.content)//1024}KB)")
                    return f"data:image/jpeg;base64,{b64}"
                elif resp.status_code == 200:
                    log.warning(f"cover small: {code} (ijavtorrent, {len(resp.content)//1024}KB, skipped)")
        except Exception:
            pass

    if cover_url or code:
        log.warning(f"cover missing: {code}")
    return ""


def _parse_video_items(html: str) -> list[dict]:
    """从 star 个人主页 HTML 中解析所有作品卡片，返回原始块列表"""
    # 找到所有 video-item 的起始位置，用位置切片避免贪婪匹配问题
    starts = [m.start() for m in re.finditer(r'<div class="col-md-4 mb-4 video-item"', html)]
    items = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else start + 8000
        block = html[start:end]

        # movie_id
        mid_match = re.search(r'data-movie-id="(\d+)"', block)
        movie_id = mid_match.group(1) if mid_match else ""

        # code
        code_match = re.search(r'href="/movie/([a-z0-9-]+)-\d+"', block)
        code = code_match.group(1).upper() if code_match else ""
        if not code or code.startswith("OAE") or code.startswith("FWAY") or code.startswith("OF") or code.startswith("REBD"):
            continue

        # title (from alt attribute)
        title_match = re.search(r'alt="([^"]+)"', block)
        title = title_match.group(1) if title_match else ""
        # Remove code prefix from title if present
        if title.upper().startswith(code + " "):
            title = title[len(code) + 1:]

        # date from mb-2
        date_match = re.search(r'<div class="mb-2">.*?([0-9]{2}/[0-9]{2}/[0-9]{4})', block, re.DOTALL)
        date_str = date_match.group(1) if date_match else ""

        # views / downloads
        views_match = re.search(r'pageview-value">([0-9,]+)', block)
        views = views_match.group(1).replace(",", "") if views_match else ""
        downloads_match = re.search(r'download-value">([0-9,]+)', block)
        downloads = downloads_match.group(1).replace(",", "") if downloads_match else ""

        # cover
        cover_match = re.search(r'data-link="(https?://[^"]+)"', block)
        cover_url = cover_match.group(1) if cover_match else ""

        # star count (from mb-1)
        mb1_match = re.search(r'<div class="mb-1">(.*?)<table class="table table-sm mt-2">', block, re.DOTALL)
        star_count = 0
        if mb1_match:
            star_count = len(re.findall(r'href="/actress/[^"]+"', mb1_match.group(1)))

        # magnets, sizes, seeds, leeches from table rows
        magnets = []
        sizes = []
        seeds = []
        leeches = []
        for row_match in re.finditer(r'<tr style="vertical-align: middle">(.*?)</tr>', block, re.DOTALL):
            row = row_match.group(1)
            magnet_match = re.search(r'href="(magnet:\?xt=[^"]+)"', row)
            if magnet_match:
                magnets.append(htmlmod.unescape(magnet_match.group(1)))
                size_match = re.search(r'fa-weight-hanging"></i>\s*([0-9.]+gb)', row, re.I)
                sizes.append(size_match.group(1) if size_match else "")
                seed_match = re.search(r'<strong>S:</strong>\s*(\d+)', row)
                seeds.append(seed_match.group(1) if seed_match else "0")
                leech_match = re.search(r'<strong>L:</strong>\s*(\d+)', row)
                leeches.append(leech_match.group(1) if leech_match else "0")

        items.append({
            "movie_id": movie_id,
            "code": code,
            "title": title,
            "date": date_str,
            "views": views,
            "downloads": downloads,
            "cover_url": cover_url,
            "star_count": star_count,
            "magnets": magnets,
            "sizes": sizes,
            "seeds": seeds,
            "leeches": leeches,
        })

    return items


def _score_magnet(magnet: str, size_str: str, seed_str: str) -> dict:
    """对单个 magnet 评分，返回评分字典"""
    res = extract_resolution(magnet)
    seed = int(seed_str) if seed_str and seed_str.isdigit() else 0
    size_mb = 0
    if size_str:
        try:
            size_mb = float(size_str.lower().replace("gb", "").strip()) * 1024
        except ValueError:
            pass

    # Resolution priority: 4K > FHDC > FHD > 1080p > HD > 720p
    res_score = 0
    if "[4K]" in res or "4k" in res.lower():
        res_score = 600
    elif "[FHDC]" in res:
        res_score = 500
    elif "[FHD]" in res:
        res_score = 400
    elif "1080p" in res:
        res_score = 300
    elif "[HD]" in res:
        res_score = 200
    elif "720p" in res:
        res_score = 100

    return {
        "magnet": magnet,
        "resolution": res,
        "size": size_str,
        "seed": seed,
        "score": res_score + seed + size_mb / 100,
    }


def _pick_best_magnet(item: dict) -> dict:
    """从多个磁力链接中挑选最佳的一个：优先 FHD/4K，否则按种子数，否则按大小"""
    magnets = item.get("magnets", [])
    if not magnets:
        return {"magnet": "", "resolution": "", "size": "", "all_magnets": []}

    scored = []
    for i, m in enumerate(magnets):
        size_str = item["sizes"][i] if i < len(item["sizes"]) else ""
        seed_str = item["seeds"][i] if i < len(item["seeds"]) else "0"
        scored.append(_score_magnet(m, size_str, seed_str))

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]
    return {
        "magnet": best["magnet"],
        "resolution": best["resolution"],
        "size": best["size"],
        "all_magnets": [s["magnet"] for s in scored],
    }


async def fetch_star(name: str, config_code: str, handle: str, star_page_url: str):
    """从star 个人主页获取单体作品数据，写入 DuckDB"""
    star_id = db.upsert_star(name=name, handle=handle, code=config_code)

    titles: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, chromium_sandbox=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        ctx = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1400, "height": 900}
        )
        page = await ctx.new_page()
        try:
            if not star_page_url:
                log.warning(f"no star_page_url for {name}, skipping")
                return {"name": name, "titles": [], "count": 0}

            await page.goto(star_page_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(2000, 4000))

            html = await page.content()
            raw_items = _parse_video_items(html)
            log.info(f"{name}: {len(raw_items)} total items on page")

            # Include all works (solo + co-star)
            log.info(f"{name}: {len(raw_items)} items (including co-stars)")

            for it in raw_items:
                best = _pick_best_magnet(it)
                titles.append({
                    "code": it["code"],
                    "title": it["title"],
                    "date": it["date"],
                    "views": it["views"],
                    "likes": it["downloads"],
                    "cover_url": it["cover_url"],
                    "magnet": best["magnet"],
                    "resolution": best["resolution"],
                    "download_url": "",
                    "all_magnets": best["all_magnets"],
                })

        except Exception as e:
            log.error(f"fetch failed: {name}: {type(e).__name__}", exc_info=True)
        finally:
            await browser.close()

    # Deduplicate + sort by date desc + take latest 3
    seen = set()
    unique = []
    for w in titles:
        if w["code"] not in seen:
            seen.add(w["code"])
            unique.append(w)

    unique.sort(
        key=lambda w: (
            w["date"].split("/")[2] + w["date"].split("/")[0] + w["date"].split("/")[1]
        ) if w["date"] else "00000000",
        reverse=True
    )
    titles = unique[:3]

    # Write to DuckDB
    for w in titles:
        code = w["code"]
        views_int = _parse_count(w["views"])
        likes_int = _parse_count(w["likes"])

        if db.title_exists(star_id, code):
            # Update metadata only, preserve existing cover
            conn = db._conn()
            row = conn.execute(
                "SELECT id, cover_b64 FROM titles WHERE star_id = ? AND code = ?",
                (star_id, code),
            ).fetchone()
            title_id, existing_cover_b64 = row if row else (None, None)
            conn.close()
            db.upsert_title(
                star_id=star_id,
                code=code,
                title=w["title"],
                release_date=w["date"],
                views=views_int,
                likes=likes_int,
                resolution=w["resolution"],
                download_url=w["download_url"],
                cover_url=w["cover_url"],
                cover_b64=existing_cover_b64,
            )
            # 存储所有 magnet：最佳标为 primary，其余备用
            for idx, m in enumerate(w.get("all_magnets", [])):
                if m:
                    db.upsert_magnet(title_id, m, is_primary=(idx == 0))
            continue

        # New title: download cover then insert
        cover_b64 = await download_cover_b64(w.get("cover_url", ""), code)
        title_id = db.upsert_title(
            star_id=star_id,
            code=code,
            title=w["title"],
            release_date=w["date"],
            views=views_int,
            likes=likes_int,
            resolution=w["resolution"],
            download_url=w["download_url"],
            cover_url=w["cover_url"],
            cover_b64=cover_b64,
        )
        for idx, m in enumerate(w.get("all_magnets", [])):
            if m:
                db.upsert_magnet(title_id, m, is_primary=(idx == 0))

    log.info(f"done: {name}: {len(titles)} titles")
    return {"name": name, "titles": titles, "count": len(titles)}


async def main():
    db.init_schema()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    config = json.load(open(config_file))
    stars = config.get("stars", [])

    log.info("fetching titles from star pages...")
    for a in stars:
        log.info(f"fetching: {a['name']}...")
        await fetch_star(
            a["name"],
            a["code"],
            a.get("handle", ""),
            a.get("star_page_url", "")
        )
        await random_delay(1.0, 2.5)

    # Stats (single GROUP BY query)
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
    for code, name, count in rows:
        log.info(f"{name}: {count} titles")
        total += count
    log.info(f"done, total {total} titles")


if __name__ == "__main__":
    asyncio.run(main())
