"""core/db — DuckDB 持久化层包

统一导出所有数据库操作，保持 backward compatibility。
"""

from .connection import DB_PATH, _conn, _apply_pragmas, _date_to_sort
from .schema import init_schema, backfill_release_date_sort
from .crud import (
    _managed_conn,
    upsert_star,
    upsert_stars,
    title_exists,
    load_all_title_codes,
    load_title_codes_missing_metadata,
    load_title_codes_missing_cover,
    _cover_dims_from_b64,
    upsert_title,
    delete_star_by_code,
    insert_sync_run,
    finish_sync_run,
    list_sync_runs,
    insert_user_events,
)
from .write_queue import DuckDBWriteQueue, db_write, get_queue
from .queries import (
    get_all_titles_json,
    export_report_json,
    get_stats,
)

__all__ = [
    "DB_PATH",
    "_conn",
    "_apply_pragmas",
    "_date_to_sort",
    "_managed_conn",
    "init_schema",
    "backfill_release_date_sort",
    "upsert_star",
    "upsert_stars",
    "title_exists",
    "load_all_title_codes",
    "load_title_codes_missing_metadata",
    "load_title_codes_missing_cover",
    "_cover_dims_from_b64",
    "upsert_title",
    "delete_star_by_code",
    "insert_sync_run",
    "finish_sync_run",
    "list_sync_runs",
    "insert_user_events",
    "get_all_titles_json",
    "export_report_json",
    "get_stats",
]
