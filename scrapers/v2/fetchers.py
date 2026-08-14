"""scrapers/v2/fetchers.py — Unified fetch layer

Supports both httpx pure HTTP and Playwright browser rendering modes,
allowing seamless switching for upper layers via protocol abstraction.
"""

from __future__ import annotations

import random
from typing import Protocol

import httpx
from playwright.async_api import async_playwright

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
]

HTTP_HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class Fetcher(Protocol):
    """Fetcher protocol"""

    async def fetch(self, url: str, **kwargs) -> str:
        """Return HTML content of the URL"""
        ...


class HttpxFetcher:
    """Pure HTTP fetcher for static pages"""

    def __init__(self, headers: dict[str, str] | None = None, timeout: float = 15.0):
        self.headers = headers or HTTP_HEADERS.copy()
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def fetch(self, url: str, **kwargs) -> str:
        client = await self._ensure_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    async def fetch_bytes(self, url: str, **kwargs) -> bytes:
        """Fetch binary content (e.g., download cover)"""
        client = await self._ensure_client()
        resp = await client.get(url, headers={**self.headers, **kwargs.get("headers", {})})
        resp.raise_for_status()
        return resp.content

    async def fetch_bytes_final_url(self, url: str, **kwargs) -> tuple[bytes, str]:
        """Fetch binary content, also returning the final URL after redirects.

        Lets callers detect redirect-to-placeholder responses (e.g. DMM's
        now_printing.jpg) that arrive as a misleading HTTP 200.
        """
        client = await self._ensure_client()
        resp = await client.get(url, headers={**self.headers, **kwargs.get("headers", {})})
        resp.raise_for_status()
        return resp.content, str(resp.url)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


class PlaywrightFetcher:
    """Browser rendering fetcher for JS dynamic pages"""

    def __init__(
        self,
        headless: bool = True,
        viewport: dict[str, int] | None = None,
        extra_args: list[str] | None = None,
        default_timeout: float = 15_000,
        wait_until: str = "domcontentloaded",
    ):
        self.headless = headless
        self.viewport = viewport or {"width": 1400, "height": 900}
        self.extra_args = extra_args or ["--no-sandbox", "--disable-setuid-sandbox"]
        self.default_timeout = default_timeout
        self.wait_until = wait_until
        self._playwright = None
        self._browser = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            chromium_sandbox=False,
            args=self.extra_args,
        )

    async def fetch(self, url: str, **kwargs) -> str:
        if self._browser is None:
            await self.start()
        ctx = await self._browser.new_context(
            user_agent=kwargs.get("user_agent", random.choice(USER_AGENTS)),
            viewport=self.viewport,
            locale="en-US",
        )
        page = await ctx.new_page()
        try:
            timeout = kwargs.get("timeout", self.default_timeout)
            wait_until = kwargs.get("wait_until", self.wait_until)
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            delay = kwargs.get("delay_ms", 0)
            if delay:
                await page.wait_for_timeout(delay)
            return await page.content()
        finally:
            await ctx.close()

    async def eval_on_selector_all(self, url: str, selector: str, js_expr: str, **kwargs) -> list:
        """Execute JS on selector after opening page"""
        if self._browser is None:
            await self.start()
        ctx = await self._browser.new_context(
            user_agent=kwargs.get("user_agent", random.choice(USER_AGENTS)),
            viewport=self.viewport,
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=kwargs.get("timeout", 15_000))
            await page.wait_for_timeout(kwargs.get("delay_ms", 2500))
            return await page.eval_on_selector_all(selector, js_expr)
        finally:
            await ctx.close()

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
