#!/usr/bin/env bash
# fetch-covers.sh — 并行下载女优封面（DMM/FANZA CDN）
# Usage: ./fetch-covers.sh [config.json]

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:-$DIR/config.json}"
OUTDIR="/tmp/actress-covers"
mkdir -p "$OUTDIR"

echo "[covers] 开始获取封面..."

CODES=($(jq -r '.actresses[].code' "$CONFIG"))
WORKFILE=$(mktemp)

for code in "${CODES[@]}"; do
  out="$OUTDIR/cover_${code}.jpg"
  [ -f "$out" ] && { echo "  ✅ $code (cached)"; continue; }
  echo "$code" >> "$WORKFILE"
done

[ ! -s "$WORKFILE" ] && { echo "[covers] 全部已缓存"; rm -f "$WORKFILE"; exit 0; }

# Parallel fetch using DMM CDN URL patterns
xargs -P4 -I{} sh -c '
  code="$1"
  c=$(echo "$code" | tr -d "-" | tr "A-Z" "a-z")
  out="/tmp/actress-covers/cover_${code}.jpg"

  # Try multiple DMM URL patterns
  for url in \
    "https://pics.dmm.co.jp/mono/movie/adult/${c}/${c}pl.jpg" \
    "https://pics.dmm.co.jp/mono/movie/adult/${c}/${c}jp-1.jpg" \
    "https://pics.dmm.co.jp/mono/movie/adult/${c}/${c}ps.jpg"; do
    status=$(curl -sL --max-time 8 -o "$out" -w "%{http_code}" "$url" 2>/dev/null)
    [ "$status" = "200" ] && { echo "  ✅ $code"; exit 0; }
  done

  # Final fallback: try www.javdatabase.net
  db_url=$(curl -sL --max-time 8 "https://www.javdatabase.net/idols/${code}/" 2>/dev/null \
    | grep -oiP "https://[^\"]+jpg" | head -1)
  if [ -n "$db_url" ]; then
    curl -sL --max-time 10 -o "$out" "$db_url" 2>/dev/null \
      && { echo "  ✅ $code (fallback)"; exit 0; }
  fi

  echo "  ⚠️  $code (no cover)"
' _ {} < "$WORKFILE"

rm -f "$WORKFILE"
count=$(ls "$OUTDIR"/*.jpg 2>/dev/null | wc -l)
echo "[covers] 完成。共 $count 张封面"
