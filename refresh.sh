#!/bin/bash
# refresh.sh — 一键抓取最新数据
#
# 用法: ./refresh.sh [config.json]
# 流程:
#   1. search-news.py → DuckDB (ijavtorrent 抓取)
#   2. fetch-jable.py → DuckDB (jable 封面+m3u8)
#   3. fetch-social.py → DuckDB (X/Twitter 最新动态)
#
# 前端通过 /api/stars 实时读取 DuckDB，无需生成静态 HTML。

set -euo pipefail

cd "$(dirname "$0")"

# ── 日志配置 ──
export LOG_DIR="${LOG_DIR:-$(pwd)/logs}"
mkdir -p "$LOG_DIR"
REFRESH_LOG="$LOG_DIR/refresh.log"
# 同时输出到终端和日志文件
exec > >(tee -a "$REFRESH_LOG") 2>&1

CONFIG="${1:-config.json}"

echo "========================================"
echo "🔄 开始刷新数据"
echo "========================================"
echo "Config : $CONFIG"
echo "LogDir : $LOG_DIR"
echo ""

# Step 1: 抓取 ijavtorrent 数据 → DuckDB
echo "[1/3] 抓取 ijavtorrent 数据..."
.venv/bin/python scrapers/search_news.py "$CONFIG"
echo "      ✓ DuckDB titles 已更新"
echo ""

# 统计
echo "========================================"
echo "✅ 刷新完成"
echo "========================================"
.venv/bin/python -c "
from core import db
s = db.get_stats()
print(f\"Stars    : {s['stars_count']}\")
print(f\"作品总数 : {s['titles_total']}\")
print(f\"Jable 条 : {s['titles_with_jable']} ({s['jable_coverage']*100:.1f}%)\")
print(f\"动态条数 : {s['social_posts']}\")
for st in s['per_star']:
    print(f\"  {st['name']:12s} : {st['titles']:3d} titles  (earliest {st['earliest']}  latest {st['latest']})\")
"
echo "日志文件 : $REFRESH_LOG"
