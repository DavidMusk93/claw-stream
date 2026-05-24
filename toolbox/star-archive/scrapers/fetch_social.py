#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "duckdb", "selectolax", "pydantic"]
# ///
"""scrapers/fetch_social.py — 兼容层

保留原有入口，内部委托 scrapers/v2/ 实现。
"""

from __future__ import annotations

import asyncio
import sys

from scrapers.v2.tasks.sync_social import run

if __name__ == "__main__":
    config = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    asyncio.run(run(config))
