#!/usr/bin/env bash
# b64-encode.sh — 封面图片转 base64 嵌入
# Usage: ./b64-encode.sh [image-dir]

DIR="${1:-/tmp/star-covers}"
OUTDIR="/tmp/star-b64"
mkdir -p "$OUTDIR"

echo "[b64] 编码封面图片..."
for img in "$DIR"/cover_*.jpg; do
  [ ! -f "$img" ] && continue
  base=$(basename "$img" .jpg)
  out="$OUTDIR/${base}.txt"
  if [ ! -f "$out" ]; then
    base64 -w0 "$img" > "$out"
    size=$(wc -c < "$out")
    echo "  ✅ $base ($(numfmt --to=iec $size)B)"
  fi
done
echo "[b64] 完成。共 $(ls "$OUTDIR"/*.txt 2>/dev/null | wc -l) 个编码"
