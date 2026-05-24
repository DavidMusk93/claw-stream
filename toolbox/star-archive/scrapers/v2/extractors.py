"""scrapers/v2/extractors.py — 统一抽取层

基于 selectolax (lexbor) 的高性能 CSS 抽取，彻底取代正则硬编码。
"""

from __future__ import annotations

import html as htmlmod
import re
import urllib.parse
from typing import Protocol

from selectolax.parser import HTMLParser

from scrapers.v2.schemas import VideoItem, MagnetCandidate, JableMeta, SocialPost


class Extractor(Protocol):
    """抽取器协议"""

    def extract(self, html: str) -> list:
        ...


def _extract_dn(magnet_url: str) -> str:
    """从 magnet dn 参数中提取原始文件名"""
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


def _extract_resolution(magnet_url: str) -> str:
    """从 magnet dn 参数中提取清晰度标记"""
    decoded = _extract_dn(magnet_url)
    if not decoded:
        return ""
    for pat in ["[4K]", "[FHDC]", "[FHD]", "[HD/720p]", "[HD]", "[720p]", "[1080p]"]:
        if pat.lower() in decoded.lower():
            return pat
    return ""


class IJavTorrentExtractor:
    """从 ijavtorrent star 个人主页抽取作品列表"""

    SKIP_CODE_PREFIXES = ("OAE", "FWAY", "OF", "REBD")

    def extract(self, html: str) -> list[VideoItem]:
        tree = HTMLParser(html)
        items: list[VideoItem] = []

        for node in tree.css(".video-item"):
            code = self._extract_code(node)
            if not code or code.startswith(self.SKIP_CODE_PREFIXES):
                continue

            title = self._extract_title(node, code)
            date = self._extract_date(node)
            views = self._extract_views(node)
            downloads = self._extract_downloads(node)
            cover_url = self._extract_cover(node)
            star_count = self._extract_star_count(node)
            magnets, sizes, seeds, leeches, resolutions = self._extract_magnets(node)

            candidates = []
            all_urls = []
            for i, m in enumerate(magnets):
                candidates.append(
                    MagnetCandidate(
                        magnet=m,
                        resolution=resolutions[i] if i < len(resolutions) else "",
                        size=sizes[i] if i < len(sizes) else "",
                        seed=int(seeds[i]) if i < len(seeds) and seeds[i].isdigit() else 0,
                        leech=int(leeches[i]) if i < len(leeches) and leeches[i].isdigit() else 0,
                    )
                )
                all_urls.append(m)

            items.append(
                VideoItem(
                    code=code,
                    title=title,
                    release_date=date,
                    views=views,
                    likes=downloads,
                    cover_url=cover_url,
                    star_count=star_count,
                    magnets=candidates,
                    all_magnet_urls=all_urls,
                )
            )

        return items

    @staticmethod
    def _extract_code(node) -> str:
        a = node.css_first('a[href^="/movie/"]')
        if not a:
            return ""
        href = a.attributes.get("href", "")
        # /movie/abc-123-456 → abc-123
        m = re.search(r'/movie/([a-z0-9-]+)-\d+', href)
        return m.group(1).upper() if m else ""

    @staticmethod
    def _extract_title(node, code: str) -> str:
        img = node.css_first("img")
        if not img:
            return ""
        alt = img.attributes.get("alt", "")
        # 去掉 code 前缀
        prefix = code + " "
        if alt.upper().startswith(prefix):
            return alt[len(prefix):]
        return alt

    @staticmethod
    def _extract_date(node) -> str | None:
        # <div class="mb-2">... 01/15/2024 ...</div>
        div = node.css_first("div.mb-2")
        if not div:
            return None
        m = re.search(r'(\d{2}/\d{2}/\d{4})', div.text(deep=True))
        return m.group(1) if m else None

    @staticmethod
    def _extract_views(node) -> int | None:
        # 用正则从文本中提取，因为 class 可能嵌套
        html_snippet = node.html
        m = re.search(r'pageview-value">([0-9,]+)', html_snippet)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_downloads(node) -> int | None:
        html_snippet = node.html
        m = re.search(r'download-value">([0-9,]+)', html_snippet)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_cover(node) -> str | None:
        a = node.css_first("a[data-link]")
        if a:
            return a.attributes.get("data-link")
        return None

    @staticmethod
    def _extract_star_count(node) -> int:
        # 在 mb-1 区域内统计 /actress/ 链接数量
        html_snippet = node.html
        # 找到 mb-1 区域（截止到 table）
        m = re.search(r'<div class="mb-1">(.*?)<table', html_snippet, re.DOTALL)
        if m:
            return len(re.findall(r'href="/actress/[^"]+"', m.group(1)))
        return 0

    @staticmethod
    def _extract_magnets(node) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
        magnets: list[str] = []
        sizes: list[str] = []
        seeds: list[str] = []
        leeches: list[str] = []
        resolutions: list[str] = []

        html_snippet = node.html
        for row_match in re.finditer(r'<tr style="vertical-align: middle">(.*?)</tr>', html_snippet, re.DOTALL):
            row = row_match.group(1)
            m = re.search(r'href="(magnet:\?xt=[^"]+)"', row)
            if not m:
                continue
            magnet_url = htmlmod.unescape(m.group(1))
            magnets.append(magnet_url)
            resolutions.append(_extract_resolution(magnet_url))

            size_m = re.search(r'fa-weight-hanging"></i>\s*([0-9.]+\s*GB)', row, re.I)
            sizes.append(size_m.group(1) if size_m else "")

            seed_m = re.search(r'<strong>S:</strong>\s*(\d+)', row)
            seeds.append(seed_m.group(1) if seed_m else "0")

            leech_m = re.search(r'<strong>L:</strong>\s*(\d+)', row)
            leeches.append(leech_m.group(1) if leech_m else "0")

        return magnets, sizes, seeds, leeches, resolutions


class JableExtractor:
    """从 jable.tv 视频页抽取 m3u8 和封面"""

    def extract(self, html: str, code: str = "") -> JableMeta:
        m3u8_list = re.findall(r'https://[^"\'\s]+\.m3u8', html)
        m3u8_url = m3u8_list[0] if m3u8_list else ""

        cover_url = ""
        m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
        if m:
            cover_url = m.group(1)
        else:
            m = re.search(r'poster="(https://assets-cdn\.jable\.tv/[^"]+)"', html)
            if m:
                cover_url = m.group(1)

        return JableMeta(code=code, m3u8_url=m3u8_url, cover_url=cover_url)


class NitterExtractor:
    """从 nitter / xcancel 页面抽取推文"""

    def extract(self, html: str) -> list[SocialPost]:
        tree = HTMLParser(html)
        posts: list[SocialPost] = []

        # nitter 镜像的选择器集合
        selectors = [
            ".timeline .timeline-item",
            ".main-tweet",
            ".tweet-body",
        ]

        nodes = []
        for sel in selectors:
            nodes = tree.css(sel)
            if nodes:
                break

        for node in nodes[:3]:
            text_el = node.css_first(".tweet-content, .tweet-text, [data-testid='tweetText']")
            time_el = node.css_first(".tweet-date a, time, .time a")

            text = text_el.text(deep=True).strip() if text_el else ""
            href = time_el.attributes.get("href", "") if time_el else ""
            time_str = ""
            if time_el:
                time_str = time_el.attributes.get("datetime", "") or time_el.text(deep=True).strip()

            if text and len(text) > 5:
                posts.append(
                    SocialPost(
                        platform="x",
                        content=text,
                        post_url=href,
                        posted_at=time_str if time_str else None,
                    )
                )

        return posts
