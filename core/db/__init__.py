"""core/db — PostgreSQL 持久化层包

统一导出所有数据库操作，保持 backward compatibility。
"""

from .connection import _conn, _date_to_sort, get_pool, close_pool
from .schema import init_schema, backfill_release_date_sort
from .crud import (
    _managed_conn,
    upsert_star,
    upsert_stars,
    load_all_title_codes,
    load_title_codes_missing_metadata,
    load_title_codes_missing_cover,
    _cover_dims_from_b64,
    _write_cover_to_disk,
    delete_star_by_code,
    insert_sync_run,
    finish_sync_run,
    list_sync_runs,
    insert_user_events,
    load_titles_for_magnet_check,
    update_magnet_check_ok,
    update_magnet_check_dead,
    swap_primary_magnet,
    list_unplayable_titles,
    delete_titles_by_ids,
)
from .write_queue import DBWriteQueue, db_write, get_queue
from .queries import get_stats

__all__ = [
    "_conn",
    "_date_to_sort",
    "get_pool",
    "close_pool",
    "_managed_conn",
    "init_schema",
    "backfill_release_date_sort",
    "upsert_star",
    "upsert_stars",
    "load_all_title_codes",
    "load_title_codes_missing_metadata",
    "load_title_codes_missing_cover",
    "_cover_dims_from_b64",
    "_write_cover_to_disk",
    "delete_star_by_code",
    "insert_sync_run",
    "finish_sync_run",
    "list_sync_runs",
    "insert_user_events",
    "load_titles_for_magnet_check",
    "update_magnet_check_ok",
    "update_magnet_check_dead",
    "swap_primary_magnet",
    "list_unplayable_titles",
    "delete_titles_by_ids",
    "get_stats",
]
