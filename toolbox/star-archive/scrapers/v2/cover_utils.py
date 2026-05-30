"""scrapers/v2/cover_utils.py — 封面下载与处理工具

从多个源获取高清封面，返回 base64 data URI。
"""

from __future__ import annotations

import asyncio
import base64
import random
import struct

from scrapers.v2.fetchers import HttpxFetcher, USER_AGENTS


def parse_image_size(data: bytes) -> tuple[int, int]:
    """解析 JPEG/PNG/WebP 图片尺寸，返回 (width, height)"""
    if len(data) < 8:
        return (0, 0)

    # JPEG
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 1:
            if data[i] == 0xFF:
                marker = data[i + 1]
                if marker == 0xD9:
                    break
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    if i + 9 < len(data):
                        h = struct.unpack(">H", data[i + 5 : i + 7])[0]
                        w = struct.unpack(">H", data[i + 7 : i + 9])[0]
                        return (w, h)
                    break
                if marker not in (0x00, 0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9):
                    if i + 3 < len(data):
                        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
                        i += 2 + seg_len
                        continue
            i += 1
        return (0, 0)

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) >= 24:
            w = struct.unpack(">I", data[16:20])[0]
            h = struct.unpack(">I", data[20:24])[0]
            return (w, h)
        return (0, 0)

    # WebP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8 " and len(data) >= 40:
            chunk_data = data[20:40]
            for i in range(len(chunk_data) - 6):
                if chunk_data[i : i + 3] == b"\x9d\x01\x2a":
                    w = int.from_bytes(chunk_data[i + 3 : i + 5], "little") & 0x3FFF
                    h = int.from_bytes(chunk_data[i + 5 : i + 7], "little") & 0x3FFF
                    return (w, h)
            return (0, 0)
        if data[12:16] == b"VP8L" and len(data) >= 25:
            bits = struct.unpack("<I", data[21:25])[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return (w, h)
        if data[12:16] == b"VP8X" and len(data) >= 30:
            w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
            h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
            return (w, h)
        return (0, 0)

    return (0, 0)


def _guess_mime(data: bytes) -> str:
    """根据二进制头部推断图片 MIME 类型。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def is_good_cover(data: bytes) -> bool:
    """检查封面是否足够高清：>= 15KB 且尺寸 >= 200x200"""
    if len(data) < 15 * 1024:
        return False
    w, h = parse_image_size(data)
    return w >= 200 and h >= 200


async def download_cover_b64(cover_url: str, code: str = "") -> str:
    """下载封面，优先高清源：DMM CDN → 给定 URL → 返回 base64 data URI 或空"""
    tried: set[str] = set()

    # 1. DMM CDN
    if code:
        c = code.lower().replace("-", "")
        dmm_url = f"https://pics.dmm.co.jp/mono/movie/adult/{c}/{c}pl.jpg"
        if dmm_url not in tried:
            tried.add(dmm_url)
            try:
                async with HttpxFetcher() as fetcher:
                    data = await fetcher.fetch_bytes(dmm_url, headers={"User-Agent": random.choice(USER_AGENTS)})
                    if data and is_good_cover(data):
                        b64 = base64.b64encode(data).decode()
                        return f"data:{_guess_mime(data)};base64,{b64}"
            except Exception:
                pass

    # 2. 给定 URL
    for url in [cover_url] if cover_url else []:
        if url in tried:
            continue
        tried.add(url)
        try:
            async with HttpxFetcher() as fetcher:
                data = await fetcher.fetch_bytes(
                    url,
                    headers={
                        "User-Agent": random.choice(USER_AGENTS),
                        "Referer": "https://ijavtorrent.com/",
                    },
                )
                if data and is_good_cover(data):
                    b64 = base64.b64encode(data).decode()
                    return f"data:{_guess_mime(data)};base64,{b64}"
        except Exception:
                pass

    return ""


async def _download_one_cover(fetcher: HttpxFetcher, code: str, cover_url: str) -> str:
    """复用 fetcher client 下载单个封面，返回 base64 data URI 或空"""
    tried: set[str] = set()

    # 1. DMM CDN
    if code:
        c = code.lower().replace("-", "")
        dmm_url = f"https://pics.dmm.co.jp/mono/movie/adult/{c}/{c}pl.jpg"
        if dmm_url not in tried:
            tried.add(dmm_url)
            try:
                data = await fetcher.fetch_bytes(dmm_url, headers={"User-Agent": random.choice(USER_AGENTS)})
                if data and is_good_cover(data):
                    b64 = base64.b64encode(data).decode()
                    return f"data:{_guess_mime(data)};base64,{b64}"
            except Exception:
                pass

    # 2. 给定 URL
    for url in [cover_url] if cover_url else []:
        if url in tried:
            continue
        tried.add(url)
        try:
            data = await fetcher.fetch_bytes(
                url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Referer": "https://ijavtorrent.com/",
                },
            )
            if data and is_good_cover(data):
                b64 = base64.b64encode(data).decode()
                return f"data:{_guess_mime(data)};base64,{b64}"
        except Exception:
            pass

    return ""


async def download_covers_batch(items: list[tuple[str, str]], concurrency: int = 8) -> dict[str, str]:
    """批量并发下载封面，返回 {code: base64_data_uri}。

    items: list of (code, cover_url)
    concurrency: 并发下载数
    """
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, str] = {}

    async with HttpxFetcher() as fetcher:
        async def _fetch_one(code: str, cover_url: str) -> None:
            async with sem:
                b64 = await _download_one_cover(fetcher, code, cover_url)
                if b64:
                    results[code] = b64

        await asyncio.gather(*[_fetch_one(code, url) for code, url in items])

    return results
