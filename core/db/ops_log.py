"""core/db/ops_log.py — DB 操作日志装饰器

自动记录所有 CRUD/查询的调用、耗时和异常。
"""

import functools
import time
from typing import Callable

from core.logger import get_logger

log = get_logger("db-ops")

# 参数中超过此长度会被截断
_MAX_ARG_LEN = 200
# 敏感/大字段名（不记录值）
_SENSITIVE_KEYS = {"cover_b64", "cover_path", "magnet", "content", "post_url"}


def _truncate(val):
    s = repr(val)
    if len(s) > _MAX_ARG_LEN:
        return s[:_MAX_ARG_LEN] + f"...({len(s)}ch)"
    return s


def _sanitize_args(func_name: str, args, kwargs) -> str:
    """生成脱敏后的参数摘要"""
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
    """装饰器：记录 DB 操作的耗时和结果"""
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
