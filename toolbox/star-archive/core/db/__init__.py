"""core/db — DuckDB 持久化层包

统一导出所有数据库操作，保持 backward compatibility。
"""

from .connection import DB_PATH, _conn, _date_to_sort
from .schema import init_schema, backfill_release_date_sort
from .crud import (
    upsert_star,
    title_exists,
    upsert_title,
    upsert_magnet,
    update_jable,
    upsert_social_post,
    delete_star_by_code,
)
from .write_queue import DuckDBWriteQueue, db_write, get_queue
from .queries import (
    get_social_posts,
    get_titles_without_jable,
    get_all_titles_json,
    export_report_json,
    get_stats,
)

__all__ = [
    "DB_PATH",
    "_conn",
    "_date_to_sort",
    "init_schema",
    "backfill_release_date_sort",
    "upsert_star",
    "title_exists",
    "upsert_title",
    "upsert_magnet",
    "update_jable",
    "upsert_social_post",
    "delete_star_by_code",
    "get_social_posts",
    "get_titles_without_jable",
    "get_all_titles_json",
    "export_report_json",
    "get_stats",
]
