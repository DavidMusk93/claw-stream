"""core/db/connection.py — 数据库连接管理与通用工具"""

import os
import duckdb

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "claw.duckdb")


def _conn():
    """获取 DuckDB 连接（单文件，每次新建连接）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return duckdb.connect(DB_PATH)


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
