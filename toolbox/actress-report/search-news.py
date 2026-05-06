#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "httpx"]
# ///
"""search-news.py — 从 ijavtorrent.com 获取作品数据（含封面 base64、magnet、清晰度）

自动过滤：排除共演/合集/写真，只保留单体作品。
封面获取策略：ijavtorrent CDN → DMM CDN → placeholder。

用法: uv run search-news.py <config.json>
"""

import sys, json, os, asyncio, re, random, base64, urllib.parse, html as htmlmod
from playwright.async_api import async_playwright
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logger import get_logger
import db

log = get_logger("search-news")

SEARCH_TERMS = {
    "白峰ミウ": "miu+shiromine",
    "夢実かなえ": "kanae+yumemi",
    "柏木ふみか": "fumika+kashiwagi",
    "森日菜子": "hinako+mori",
    "miru": "miru",
    "金松季歩": "kiho+kanematsu",
    "神木玲": "rei+kamiki",
    "瀧本雫葉": "shizuha+kitamoto",
    "天宮響": "hibiki+amamiya",
    "楓カレン": "karen+kaede",
    "美ノ瀬すずめ": "suzume+mino",
    "涼森れむ": "remu+suzumori",
    "瀬戸環奈": "kanna+seto",
}

KNOWN_NAMES = {
    "白峰ミウ": "Miu Shiromine",
    "夢実かなえ": "Kanae Yumemi",
    "柏木ふみか": "Fumika Kashiwagi",
    "森日菜子": "Mori Hinako",
    "miru": "miru",
    "金松季歩": "Kiho Kanematsu",
    "神木玲": "Rei Kamiki",
    "瀧本雫葉": "Shizuha Kitamoto",
    "天宮響": "Hibiki Amamiya",
    "楓カレン": "Karen Kaede",
    "美ノ瀬すずめ": "Suzume Mino",
    "涼森れむ": "Remu Suzumori",
    "瀬戸環奈": "Kanna Seto",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
]


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


def extract_resolution(magnet_url: str) -> str:
    """从 magnet dn 参数中提取清晰度（含 HTML entity 解码）"""
    if not magnet_url:
        return ""
    decoded_url = htmlmod.unescape(magnet_url)
    parsed = urllib.parse.urlparse(decoded_url)
    params = urllib.parse.parse_qs(parsed.query)
    dn = params.get("dn", [""])[0]
    if not dn:
        return ""
    decoded = urllib.parse.unquote(dn)
    for pat in ["[4K]", "[FHDC]", "[FHD]", "[HD/720p]", "[HD]", "[720p]", "[1080p]"]:
        if pat.lower() in decoded.lower():
            return pat
    return ""


def is_solo_work(actress_line: str) -> bool:
    if "blu-ray" in actress_line.lower() or "bluray" in actress_line.lower():
        return False
    if "monster" in actress_line.lower():
        return False
    names = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", actress_line)
    if len(names) <= 2:
        return True
    tags = ["Top", "Daily", "Solowork", "Creampie", "Big Tits", "Slut", "Slender", "Titty", "Blow", "Squirting", "Masturbation"]
    if any(t.lower() in actress_line.lower() for t in tags):
        return True
    return False


async def download_cover_b64(cover_url: str, code: str = "") -> str:
    """下载封面，尝试 ijavtorrent CDN → DMM CDN → 返回 base64 或空"""
    tried = set()

    for url in [cover_url] if cover_url else []:
        if url in tried:
            continue
        tried.add(url)
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": random.choice(USER_AGENTS)})
                if resp.status_code == 200 and len(resp.content) > 1000:
                    b64 = base64.b64encode(resp.content).decode()
                    log.info(f"cover ok: {code} ({len(resp.content)//1024}KB, {url.split('/')[-1][:30]})")
                    return f"data:image/jpeg;base64,{b64}"
        except Exception:
            pass

    if code:
        c = code.lower().replace("-", "")
        dmm_url = f"https://pics.dmm.co.jp/mono/movie/adult/{c}/{c}pl.jpg"
        if dmm_url not in tried:
            try:
                async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                    resp = await client.get(dmm_url, headers={"User-Agent": random.choice(USER_AGENTS)})
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        b64 = base64.b64encode(resp.content).decode()
                        log.info(f"cover ok: {code} (DMM fallback, {len(resp.content)//1024}KB)")
                        return f"data:image/jpeg;base64,{b64}"
            except Exception:
                pass

    if cover_url:
        log.warning(f"cover missing: {code}")
    return ""


async def fetch_actress(name: str, config_code: str, handle: str):
    """获取单个女优的作品数据，写入 DuckDB"""
    actress_id = db.upsert_actress(name=name, handle=handle, code=config_code)

    search_term = SEARCH_TERMS.get(name, handle.replace("_", "+"))
    target_name = KNOWN_NAMES.get(name, name)
    target_parts = target_name.lower().split()
    works = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, chromium_sandbox=False, args=["--no-sandbox", "--disable-setuid-sandbox"])
        ctx = await browser.new_context(user_agent=random.choice(USER_AGENTS), viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        try:
            url = f"https://ijavtorrent.com/?searchTerm={search_term}&sortby=created"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(random.randint(1500, 3000))

            html = await page.content()
            body = await page.inner_text("body")
            stripped = [l.strip() for l in body.split("\n")]

            movies = {}
            for m in re.finditer(r"sendEvent\('Cover Click','Main List Movie','(\d+)'\).*?data-link=\"(https?://[^\"]+)\"", html, re.DOTALL):
                mid = m.group(1)
                movies[mid] = {"cover": m.group(2), "magnets": [], "downloads": []}
            for m in re.finditer(r"sendEvent\('Magnet Click','Main List Movie','(\d+)'\).*?href=\"(magnet:\?xt=urn:btih:[^\"]+)\"", html, re.DOTALL):
                mid = m.group(1)
                if mid in movies:
                    movies[mid]["magnets"].append(m.group(2))
            for m in re.finditer(r"sendEvent\('Torrent Click','Main List Movie','(\d+)'\).*?href=\"(/download/\d+)\"", html, re.DOTALL):
                mid = m.group(1)
                if mid in movies:
                    movies[mid]["downloads"].append("https://ijavtorrent.com" + m.group(2))

            code_re = re.compile(r"\b([A-Z]{2,8}-\d+)\b", re.I)
            text_entries = []
            for i, line in enumerate(stripped):
                cm = code_re.search(line)
                if not cm:
                    continue
                c = cm.group(1).upper()
                title = line[cm.end():].strip()
                date_str = views = likes = ""
                name_line = ""
                for j in range(i + 1, min(i + 5, len(stripped))):
                    nl = stripped[j]
                    dm = re.search(r"(\d{2}/\d{2}/\d{4})", nl)
                    if dm:
                        parts = nl.split("|")
                        date_str = parts[0].strip()
                        if len(parts) > 1:
                            views = parts[1].strip()
                        if len(parts) > 2:
                            likes = parts[2].strip()
                    if target_parts and all(p in nl.lower() for p in target_parts):
                        name_line = nl
                text_entries.append({"code": c, "title": title[:200], "date": date_str, "views": views, "likes": likes, "name_line": name_line})

            movie_ids = list(movies.keys())
            for idx, entry in enumerate(text_entries):
                if entry["name_line"] and not is_solo_work(entry["name_line"]):
                    continue
                c = entry["code"]
                if c.startswith("OAE") or c.startswith("FWAY") or c.startswith("OF") or c.startswith("REBD"):
                    continue
                movie = movies.get(movie_ids[idx] if idx < len(movie_ids) else "", {})
                magnet = htmlmod.unescape((movie.get("magnets") or [""])[0])
                works.append({
                    "code": c,
                    "title": entry["title"],
                    "date": entry["date"],
                    "views": entry["views"],
                    "likes": entry["likes"],
                    "cover_url": movie.get("cover", ""),
                    "magnet": magnet,
                    "resolution": extract_resolution(magnet),
                    "download_url": (movie.get("downloads") or [""])[0],
                })

        except Exception as e:
            log.error(f"fetch failed: {name}: {type(e).__name__}", exc_info=True)
        finally:
            await browser.close()

    # 去重 + 排序 + 取最新3
    seen = set()
    unique = []
    for w in works:
        if w["code"] not in seen:
            seen.add(w["code"])
            unique.append(w)
    unique.sort(key=lambda w: (w["date"].split("/")[2] + w["date"].split("/")[1] + w["date"].split("/")[0]) if w["date"] else "00000000", reverse=True)
    works = unique[:3]

    # 写入 DuckDB
    for w in works:
        code = w["code"]
        views_int = _parse_count(w["views"])
        likes_int = _parse_count(w["likes"])

        if db.work_exists(actress_id, code):
            # 仅更新元数据，保留已有封面
            conn = db._conn()
            row = conn.execute(
                "SELECT id, cover_b64 FROM works WHERE actress_id = ? AND code = ?",
                (actress_id, code),
            ).fetchone()
            work_id, existing_cover_b64 = row if row else (None, None)
            conn.close()
            db.upsert_work(
                actress_id=actress_id,
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
            if w["magnet"] and work_id:
                db.upsert_magnet(work_id, w["magnet"])
            continue

        # 新作品：下载封面后写入
        cover_b64 = await download_cover_b64(w.get("cover_url", ""), code)
        work_id = db.upsert_work(
            actress_id=actress_id,
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
        if w["magnet"]:
            db.upsert_magnet(work_id, w["magnet"])

    log.info(f"done: {name}: {len(works)} works")
    return {"name": name, "target_name": target_name, "works": works, "count": len(works)}


async def main():
    db.init_schema()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    config = json.load(open(config_file))
    actresses = config.get("actresses", [])

    log.info("fetching works data...")
    for a in actresses:
        log.info(f"fetching: {a['name']}...")
        await fetch_actress(a["name"], a["code"], a["handle"])
        await random_delay(1.0, 2.5)

    # 统计
    total = 0
    for a in actresses:
        conn = db._conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM works w JOIN actresses act ON w.actress_id = act.id WHERE act.code = ?",
            (a["code"],),
        ).fetchone()[0]
        conn.close()
        log.info(f"{a['name']}: {count} works")
        total += count
    log.info(f"done, total {total} works")


if __name__ == "__main__":
    asyncio.run(main())
