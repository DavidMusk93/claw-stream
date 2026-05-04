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

OUTDIR = "/tmp/actress-news"

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


async def random_delay(min_s=1.0, max_s=2.5):
    await asyncio.sleep(random.uniform(min_s, max_s))


def extract_resolution(magnet_url: str) -> str:
    """从 magnet dn 参数中提取清晰度（含 HTML entity 解码）"""
    if not magnet_url:
        return ""
    # 解码 HTML entities（magnet URL 经过双重 &amp; 转义）
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
                    print(f"    cover ✅ {code} ({len(resp.content)//1024}KB, {url.split('/')[-1][:30]})", file=sys.stderr)
                    return f"data:image/jpeg;base64,{b64}"
        except Exception:
            pass

    # DMM CDN fallback
    if code:
        c = code.lower().replace("-", "")
        dmm_url = f"https://pics.dmm.co.jp/mono/movie/adult/{c}/{c}pl.jpg"
        if dmm_url not in tried:
            try:
                async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                    resp = await client.get(dmm_url, headers={"User-Agent": random.choice(USER_AGENTS)})
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        b64 = base64.b64encode(resp.content).decode()
                        print(f"    cover ✅ {code} (DMM fallback, {len(resp.content)//1024}KB)", file=sys.stderr)
                        return f"data:image/jpeg;base64,{b64}"
            except Exception:
                pass

    if cover_url:
        print(f"    cover ⚠️ {code} (no cover)", file=sys.stderr)
    return ""


async def fetch_actress(name: str, config_code: str, handle: str):
    """获取单个女优的作品数据"""
    os.makedirs(OUTDIR, exist_ok=True)
    outfile = os.path.join(OUTDIR, f"{config_code}.json")

    if os.path.exists(outfile):
        mtime = os.path.getmtime(outfile)
        if __import__("time").time() - mtime < 3600:
            with open(outfile) as f:
                return json.load(f)

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

            # 解析 movies
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

            # 文本扫描
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
                        if len(parts) > 1: views = parts[1].strip()
                        if len(parts) > 2: likes = parts[2].strip()
                    if target_parts and all(p in nl.lower() for p in target_parts):
                        name_line = nl
                text_entries.append({"code": c, "title": title[:200], "date": date_str, "views": views, "likes": likes, "name_line": name_line})

            # 合并：搜索词已过滤，name_line 仅用于 is_solo_work 检查；如果 name_line 为空也保留（避免拼写差异漏掉）
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
            print(f"  ⚠️ {name}: {type(e).__name__}", file=sys.stderr)
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

    # 下载封面（并行）
    print(f"    downloading covers for {name}...", file=sys.stderr)
    tasks = [download_cover_b64(w.get("cover_url", ""), w["code"]) for w in works]
    b64_list = await asyncio.gather(*tasks)
    for w, b64 in zip(works, b64_list):
        w["cover_b64"] = b64

    data = {
        "name": name,
        "target_name": target_name,
        "works": works,
        "count": len(works),
    }
    json.dump(data, open(outfile, "w"), ensure_ascii=False, indent=2)
    print(f"  ✅ {name}: {len(works)} 作品, {sum(1 for w in works if w['cover_b64'])} 封面", file=sys.stderr)
    return data


async def main():
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    config = json.load(open(config_file))
    actresses = config.get("actresses", [])

    print("[news] 获取作品数据...", file=sys.stderr)
    for a in actresses:
        print(f"  → {a['name']}...", file=sys.stderr)
        await fetch_actress(a["name"], a["code"], a["handle"])
        await random_delay(1.0, 2.5)

    total = 0
    for a in actresses:
        f = os.path.join(OUTDIR, f"{a['code']}.json")
        try:
            d = json.load(open(f))
            n = len(d.get("works", []))
            print(f"  {a['name']}: {n} 条")
            total += n
        except:
            pass
    print(f"[news] 完成，共 {total} 个作品", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
