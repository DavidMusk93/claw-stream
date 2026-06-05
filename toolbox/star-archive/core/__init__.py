"""core — 共享核心模块

包含数据库层 (db) 和日志 (logger)。
"""

from core.db import init_schema, _conn, upsert_star, title_exists, upsert_title
from core.db import get_all_titles_json
from core.logger import get_logger, capture_stdout, set_trace_id, get_trace_id

__all__ = [
    "init_schema",
    "_conn",
    "upsert_star",
    "title_exists",
    "upsert_title",
    "get_all_titles_json",
    "get_logger",
    "capture_stdout",
]
