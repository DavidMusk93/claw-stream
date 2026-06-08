#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright", "httpx", "duckdb", "selectolax", "pydantic"]
# ///
"""scrapers/v2/cli.py — 统一爬虫入口

用法:
    python -m scrapers.v2.cli sync-titles  [config.json]
"""

from __future__ import annotations

import asyncio
import sys

from scrapers.v2.tasks.sync_titles import run as run_titles


COMMANDS = {
    "sync-titles": run_titles,
}


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python -m scrapers.v2.cli <{'|'.join(COMMANDS)}> [config.json]")
        sys.exit(1)

    cmd = sys.argv[1]
    config = sys.argv[2] if len(sys.argv) > 2 else "config.json"

    runner = COMMANDS.get(cmd)
    if not runner:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    asyncio.run(runner(config))


if __name__ == "__main__":
    main()
