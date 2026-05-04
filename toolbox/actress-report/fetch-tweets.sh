#!/usr/bin/env bash
# fetch-tweets.sh — 获取女优 X 最近的动态
# Usage: ./fetch-tweets.sh [handle ...]
# 支持 Nitter / xcancel / 直接 web_fetch

HANDLES=("$@")
[ ${#HANDLES[@]} -eq 0 ] && {
  echo "Usage: $0 <handle1> [handle2 ...]"
  exit 1
}

OUTDIR="/tmp/actress-tweets"
mkdir -p "$OUTDIR"

echo "[tweets] 获取最近的动态..."

for handle in "${HANDLES[@]}"; do
  out="$OUTDIR/${handle}.json"
  [ -f "$out" ] && { echo "  ✅ $handle (cached)"; continue; }

  # Try Nitter first
  html=$(curl -sL --max-time 10 "https://nitter.net/${handle}" 2>/dev/null || true)
  if [ -z "$html" ]; then
    # Fallback to xcancel
    html=$(curl -sL --max-time 10 "https://xcancel.com/${handle}" 2>/dev/null || true)
  fi

  if [ -n "$html" ]; then
    # 提取最近 2 条推文
    echo "$html" | grep -oP '(?<=class="tweet-content">).*?(?=</div>)' | head -2 > "$out"
    echo "  ✅ $handle ($(wc -l < "$out") tweets)"
  else
    echo '[]' > "$out"
    echo "  ⚠️  $handle (unavailable)"
  fi
done

echo "[tweets] 完成"
