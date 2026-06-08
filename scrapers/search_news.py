#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "httpx", "duckdb", "selectolax", "pydantic"]
# ///
"""scrapers/search_news.py — Compatibility layer

Preserve original entrypoints and function exports, internally delegate to scrapers/v2/ implementation.
"""

from __future__ import annotations

import asyncio
import sys

# Preserve old import path compatibility
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
