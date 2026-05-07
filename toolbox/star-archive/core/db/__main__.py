"""core/db/__main__.py — CLI 入口

用法:
    python3 -m core.db              # 初始化 schema
    python3 -m core.db backfill     # 回填 release_date_sort
    python3 -m core.db stats        # 输出统计信息
"""

import json
import sys

from .schema import init_schema, backfill_release_date_sort
from .queries import get_stats
from .connection import DB_PATH


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export_to_tmp":
        print("[db] export_to_tmp removed; generate-report.js reads DuckDB directly via Node.js driver")
    elif len(sys.argv) > 1 and sys.argv[1] == "backfill":
        init_schema()
        backfill_release_date_sort()
        print("[db] backfill done")
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(get_stats(), ensure_ascii=False, indent=2))
    else:
        init_schema()
        print(f"[db] initialized: {DB_PATH}")
