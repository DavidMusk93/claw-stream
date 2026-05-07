"""scrapers/base.py — 爬虫共享基础设施

提供配置加载、Playwright 统一启动、日志初始化等通用能力。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# 将项目根目录加入 sys.path，使 core/ 可被导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core import get_logger


def _ensure_logger(name: str):
    """延迟初始化日志器（首次调用时创建）"""
    return get_logger(name)


class ConfigLoader:
    """加载 config.json，提供 stars 列表访问"""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(_PROJECT_ROOT, "config.json")
        self._data: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._data is None:
            with open(self.path, encoding="utf-8") as f:
                self._data = json.load(f)
        return self._data

    @property
    def stars(self) -> list[dict[str, Any]]:
        return self.load().get("stars", [])

    def star_by_code(self, code: str) -> dict[str, Any] | None:
        for s in self.stars:
            if s.get("code") == code:
                return s
        return None


class PlaywrightMixin:
    """统一 Playwright 浏览器启动参数"""

    HEADLESS = True
    SANDBOX = False
    ARGS = ["--no-sandbox", "--disable-setuid-sandbox"]
    VIEWPORT = {"width": 1400, "height": 900}

    @classmethod
    async def launch_browser(cls, p):
        """返回 (browser, context) 元组"""
        from playwright.async_api import async_playwright

        browser = await p.chromium.launch(
            headless=cls.HEADLESS,
            chromium_sandbox=cls.SANDBOX,
            args=cls.ARGS,
        )
        ctx = await browser.new_context(
            viewport=cls.VIEWPORT,
        )
        return browser, ctx
