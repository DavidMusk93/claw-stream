"""core/db/connection.py — Database connection management and utilities"""

from __future__ import annotations

import os
import time

import duckdb

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


def _conn(max_retries: int = 5, retry_delay: float = 0.5):
    """Get DuckDB connection (single-file, new connection each time) with lock-conflict retry."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    for attempt in range(max_retries):
        try:
            return duckdb.connect(DB_PATH)
        except duckdb.IOException as exc:
            if "Could not set lock" in str(exc) and attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise


def _date_to_sort(date_str: str | None) -> str | None:
    """Convert dd/mm/YYYY to YYYYMMDD for correct sorting"""
    if not date_str:
        return None
    try:
        parts = date_str.split("/")
        if len(parts) == 3:
            # parts[0]=dd, parts[1]=mm, parts[2]=YYYY
            return f"{parts[2]}{parts[1].zfill(2)}{parts[0].zfill(2)}"
    except Exception:
        pass
    return None
