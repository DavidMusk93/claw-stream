"""core — 共享核心模块

包含数据库层 (db) 和日志 (logger)。
"""

from core.db import init_schema, _conn, upsert_star
from core.logger import get_logger, capture_stdout, set_trace_id, get_trace_id

__all__ = [
    "init_schema",
    "_conn",
    "upsert_star",
    "get_logger",
    "capture_stdout",
]
