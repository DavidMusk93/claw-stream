"""core/db/connection.py — Database connection management and utilities"""

from __future__ import annotations

import os
import time

import duckdb

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


# DuckDB defaults its buffer manager to 80% of RAM (~3.1GB on this 4GB box).
# The titles table carries multi-hundred-KB base64 cover blobs inline, so a
# large scan/UPSERT can blow past the remaining memory and get the backend
# OOM-killed. Cap every connection and spill to disk instead.
DUCKDB_MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "1GB")
DUCKDB_TEMP_DIR = os.environ.get("DUCKDB_TEMP_DIR", "/tmp/duckdb-spill")


def _apply_pragmas(conn) -> None:
    os.makedirs(DUCKDB_TEMP_DIR, exist_ok=True)
    conn.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT}'")
    conn.execute(f"SET temp_directory='{DUCKDB_TEMP_DIR}'")


def _conn(max_retries: int = 5, retry_delay: float = 0.5):
    """Get DuckDB connection (single-file, new connection each time) with lock-conflict retry."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    for attempt in range(max_retries):
        try:
            conn = duckdb.connect(DB_PATH)
            _apply_pragmas(conn)
            return conn
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
