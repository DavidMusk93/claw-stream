#!/usr/bin/env bash
# run.sh — 一键生成女优报告
# Usage: ./run.sh [config.json] [out.html]
# 流程：fetch covers → b64 encode → search news → generate HTML

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:-$DIR/config.json}"
OUT="${2:-/root/.openclaw/workspace/actresses-report.html}"
START=$(date +%s)

log() { echo "[$(date '+%H:%M:%S')] $1"; }

log "🚀 开始生成报告"

# Step 1: Fetch covers
log "📸 下载封面..."
bash "$DIR/fetch-covers.sh" "$CONFIG"

# Step 2: Base64 encode
log "🔐 编码封面..."
bash "$DIR/b64-encode.sh"

# Step 3: Search latest news (搜索引擎获取最近的动态)
log "🔍 搜索最近的动态..."
if command -v uv &>/dev/null && [ -f "$DIR/pyproject.toml" ]; then
  uv run "$DIR/search-news.py" "$CONFIG" 2>/dev/null || echo "[news] ⚠️ 搜索失败，跳过"
fi

# Step 4: Generate HTML
log "📄 生成 HTML..."
node "$DIR/generate-report.js" "$CONFIG" "$OUT"

ELAPSED=$(( $(date +%s) - START ))
log "✅ 完成！耗时 ${ELAPSED}s → $OUT"
