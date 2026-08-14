"""tests/test_cover_utils.py — DMM CDN candidate URL generation & placeholder guard"""

from __future__ import annotations

import asyncio

from scrapers.v2.cover_utils import _dmm_candidate_urls, _try_dmm


def test_candidate_urls_padded_digital_variants():
    urls = _dmm_candidate_urls("SIVR-490")
    assert urls[0].endswith("/mono/movie/adult/sivr490/sivr490pl.jpg")
    assert "https://pics.dmm.co.jp/digital/video/sivr00490/sivr00490pl.jpg" in urls
    # unpadded digital variant is appended after padded ones
    assert "https://pics.dmm.co.jp/digital/video/sivr490/sivr490pl.jpg" in urls
    assert len(urls) == len(set(urls))  # no duplicates


def test_candidate_urls_maker_prefixes():
    urls = _dmm_candidate_urls("DSVR-1669")
    assert "https://pics.dmm.co.jp/digital/video/13dsvr01669/13dsvr01669pl.jpg" in urls
    urls = _dmm_candidate_urls("FAVR-002")
    assert "https://pics.dmm.co.jp/digital/video/1favr00002/1favr00002pl.jpg" in urls


def test_candidate_urls_invalid_code():
    assert _dmm_candidate_urls("") == []
    assert _dmm_candidate_urls("no-digits") == []


class _FakeFetcher:
    """Mimics HttpxFetcher.fetch_bytes_final_url with scripted responses."""

    def __init__(self, hits: dict[str, bytes]):
        # hits: url substring -> body; anything else "redirects" to now_printing
        self._hits = hits

    async def fetch_bytes_final_url(self, url: str, **kwargs):
        for sub, body in self._hits.items():
            if sub in url:
                return body, url
        return b"placeholder", "https://imgsrc.dmm.com/pics/mono/movie/n/now_printing/now_printing.jpg"


def _jpeg_bytes(width: int = 800, height: int = 800) -> bytes:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    # pad above the 15KB is_good_cover threshold
    return data + b"\0" * (16 * 1024 - len(data)) if len(data) < 16 * 1024 else data


def test_try_dmm_skips_now_printing_placeholder():
    good = _jpeg_bytes()
    fetcher = _FakeFetcher({"/13dsvr01669pl.jpg": good})
    b64 = asyncio.run(_try_dmm(fetcher, "DSVR-1669"))
    assert b64.startswith("data:image/jpeg;base64,")


def test_try_dmm_all_placeholder_returns_empty():
    fetcher = _FakeFetcher({})
    assert asyncio.run(_try_dmm(fetcher, "SIVR-490")) == ""
