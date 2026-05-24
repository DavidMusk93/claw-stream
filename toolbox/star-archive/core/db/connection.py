"""core/db/connection.py — 数据库连接管理与通用工具"""

from __future__ import annotations

import os
import time

import duckdb

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


def _conn(max_retries: int = 5, retry_delay: float = 0.5):
    """获取 DuckDB 连接（单文件，每次新建连接），带锁冲突重试。"""
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
    """将 dd/mm/YYYY 转为 YYYYMMDD 用于正确排序"""
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
