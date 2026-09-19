"""core/db/connection.py — PostgreSQL connection pool and utilities"""

from __future__ import annotations

import os
import threading

import psycopg
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10


def get_pool() -> ConnectionPool:
    """Lazily create the module-level connection pool.

    Connections are created with autocommit=True to match the implicit
    autocommit semantics of the previous DuckDB driver; explicit multi-statement
    writes wrap themselves in `conn.transaction()`.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                dsn = os.environ.get("CLAW_PG_DSN")
                if not dsn:
                    raise RuntimeError("CLAW_PG_DSN environment variable is not set")
                _pool = ConnectionPool(
                    conninfo=dsn,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                    kwargs={"autocommit": True},
                    # close() on a pooled connection returns it to the pool
                    # (psycopg_pool >= 3.3); required by the _managed_conn
                    # pattern, which close()es after every operation.
                    close_returns=True,
                    open=True,
                )
    return _pool


def _conn() -> psycopg.Connection:
    """Get a connection from the pool; .close() returns it to the pool."""
    return get_pool().getconn()


def close_pool() -> None:
    """Close the pool (process shutdown / tests)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


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
