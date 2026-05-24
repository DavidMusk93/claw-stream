#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "httpx", "duckdb", "selectolax", "pydantic"]
# ///
"""scrapers/search_news.py — 兼容层

保留原有入口与函数导出，内部委托 scrapers/v2/ 实现。
"""

from __future__ import annotations

import asyncio
import sys

# 保留旧 import 路径兼容性
from scrapers.v2.cover_utils import parse_image_size, download_cover_b64, is_good_cover
from scrapers.v2.tasks.sync_titles import run

__all__ = [
    "run",
    "download_cover_b64",
    "parse_image_size",
    "is_good_cover",
]

if __name__ == "__main__":
    config = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    asyncio.run(run(config))
