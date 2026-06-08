"""core/db/ops_log.py — DB operation logging decorator

Automatically records all CRUD/query calls, elapsed time, and exceptions.
"""

from __future__ import annotations

import functools
import time
from typing import Callable

from core.logger import get_logger

log = get_logger("db-ops")

# Arguments longer than this will be truncated
_MAX_ARG_LEN = 200
# Sensitive / large field names (values not logged)
_SENSITIVE_KEYS = {"cover_b64", "cover_path", "magnet", "content", "post_url"}


def _truncate(val):
    s = repr(val)
    if len(s) > _MAX_ARG_LEN:
        return s[:_MAX_ARG_LEN] + f"...({len(s)}ch)"
    return s


def _sanitize_args(func_name: str, args, kwargs) -> str:
    """Generate a sanitized argument summary"""
    parts = []
    for i, v in enumerate(args):
        key = f"arg{i}"
        if v is None:
            parts.append(f"{key}=None")
        elif key in _SENSITIVE_KEYS or (isinstance(v, str) and len(v) > _MAX_ARG_LEN):
            parts.append(f"{key}=<len {len(v)}>")
        else:
            parts.append(f"{key}={_truncate(v)}")
    for k, v in kwargs.items():
        if v is None:
            parts.append(f"{k}=None")
        elif k in _SENSITIVE_KEYS or (isinstance(v, str) and len(v) > _MAX_ARG_LEN):
            parts.append(f"{k}=<len {len(v)}>")
        else:
            parts.append(f"{k}={_truncate(v)}")
    return " ".join(parts) if parts else "-"


def trace_db(func: Callable) -> Callable:
    """Decorator: record DB operation elapsed time and result"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        sig = _sanitize_args(func.__name__, args, kwargs)
        try:
            result = func(*args, **kwargs)
            elapsed = (time.perf_counter() - start) * 1000
            log.debug(f"{func.__name__} {sig} -> ok ({elapsed:.1f}ms)")
            return result
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            log.error(
                f"{func.__name__} {sig} -> {type(exc).__name__}: {exc} ({elapsed:.1f}ms)",
                exc_info=True,
            )
            raise
    return wrapper
