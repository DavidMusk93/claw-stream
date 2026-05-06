#!/bin/bash
# refresh.sh — 一键抓取最新数据并重排生成报告
#
# 用法: ./refresh.sh [config.json]
# 流程:
#   1. search-news.py → DuckDB (ijavtorrent 抓取)
#   2. fetch-jable.py → DuckDB (jable 封面+m3u8)
#   3. fetch-social.py → DuckDB (X/Twitter 最新动态)
#   4. generate-report.js → ../../actresses-report.html (直接从 DuckDB 读取)
#
# 日志:
#   设置 LOG_DIR 环境变量后，各组件日志自动汇聚。
#   本脚本的完整终端输出同时写入 logs/YYYY-MM-DD/refresh.log

set -euo pipefail

cd "$(dirname "$0")"

# ── 日志配置 ──
export LOG_DIR="${LOG_DIR:-$(pwd)/logs}"
mkdir -p "$LOG_DIR"
REFRESH_LOG="$LOG_DIR/refresh.log"
# 同时输出到终端和日志文件
exec > >(tee -a "$REFRESH_LOG") 2>&1

CONFIG="${1:-config.json}"
REPORT_DIR="$(cd ../.. && pwd)"
REPORT_OUT="${REPORT_DIR}/actresses-report.html"

echo "========================================"
echo "🔄 开始刷新 actress report"
echo "========================================"
echo "Config : $CONFIG"
echo "Output : $REPORT_OUT"
echo "LogDir : $LOG_DIR"
echo ""

# Step 1: 抓取 ijavtorrent 数据 → DuckDB
echo "[1/4] 抓取 ijavtorrent 数据..."
if command -v uv >/dev/null 2>&1; then
    uv run search-news.py "$CONFIG"
else
    python3 search-news.py "$CONFIG"
fi
echo "      ✓ DuckDB works 已更新"
echo ""

# Step 2: 抓取 jable 封面+m3u8 → DuckDB
echo "[2/4] 抓取 jable.tv 数据..."
python3 fetch-jable.py "$CONFIG"
echo "      ✓ DuckDB jable 已更新"
echo ""

# Step 3: 抓取社交动态 → DuckDB
echo "[3/4] 抓取 X/Twitter 动态..."
if command -v uv >/dev/null 2>&1; then
    uv run fetch-social.py "$CONFIG"
else
    python3 fetch-social.py "$CONFIG"
fi
echo "      ✓ DuckDB social 已更新"
echo ""

# Step 4: 生成报告
echo "[4/4] 生成 HTML 报告..."
export LOG_DIR  # 传递给 generate-report.js
node generate-report.js "$CONFIG" "$REPORT_OUT"
echo "      ✓ $REPORT_OUT 已生成"
echo ""

# 统计
echo "========================================"
echo "✅ 刷新完成"
echo "========================================"
# 统计 DuckDB 中的数据
WORKS_COUNT=$(python3 -c "import db; c=db._conn(); n=c.execute('SELECT COUNT(*) FROM works').fetchone()[0]; c.close(); print(n)")
JABLE_COUNT=$(python3 -c "import db; c=db._conn(); n=c.execute('SELECT COUNT(*) FROM works WHERE jable_m3u8 IS NOT NULL').fetchone()[0]; c.close(); print(n)")
SOCIAL_COUNT=$(python3 -c "import db; c=db._conn(); n=c.execute('SELECT COUNT(*) FROM social_posts').fetchone()[0]; c.close(); print(n)")
echo "作品总数 : $WORKS_COUNT"
echo "Jable 条 : $JABLE_COUNT"
echo "动态条数 : $SOCIAL_COUNT"
echo "日志文件 : $REFRESH_LOG"
