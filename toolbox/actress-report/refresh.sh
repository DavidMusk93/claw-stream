#!/bin/bash
# refresh.sh — 一键抓取最新数据并重排生成报告
#
# 用法: ./refresh.sh [config.json]
# 流程:
#   1. search-news.py → /tmp/actress-news/ (ijavtorrent 抓取)
#   2. fetch-jable.py → /tmp/actress-jable/ (jable 封面+m3u8)
#   3. generate-report.js → ../../actresses-report.html
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

# Step 1: 抓取 ijavtorrent 数据
echo "[1/3] 抓取 ijavtorrent 数据..."
if command -v uv >/dev/null 2>&1; then
    uv run search-news.py "$CONFIG"
else
    python3 search-news.py "$CONFIG"
fi
echo "      ✓ /tmp/actress-news/ 已更新"
echo ""

# Step 2: 抓取 jable 封面+m3u8
echo "[2/3] 抓取 jable.tv 数据..."
python3 fetch-jable.py "$CONFIG"
echo "      ✓ /tmp/actress-jable/ 已更新"
echo ""

# Step 3: 生成报告
echo "[3/3] 生成 HTML 报告..."
export LOG_DIR  # 传递给 generate-report.js
node generate-report.js "$CONFIG" "$REPORT_OUT"
echo "      ✓ $REPORT_OUT 已生成"
echo ""

# 统计
echo "========================================"
echo "✅ 刷新完成"
echo "========================================"
NEWS_COUNT=$(ls /tmp/actress-news/*.json 2>/dev/null | wc -l)
JABLE_COUNT=$(ls /tmp/actress-jable/*.json 2>/dev/null | wc -l)
echo "新闻条目 : $NEWS_COUNT"
echo "Jable 条 : $JABLE_COUNT"
echo "日志文件 : $REFRESH_LOG"
