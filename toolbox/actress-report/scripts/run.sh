#!/bin/bash
# run.sh — 启动 Actress Report 完整服务
#
# 用法: ./scripts/run.sh
# 启动:
#   - FastAPI backend (port 8765)
#   - Nuxt frontend   (port 3000)

set -euo pipefail

cd "$(dirname "$0")/.."

export LOG_DIR="${LOG_DIR:-$(pwd)/logs}"
mkdir -p "$LOG_DIR"

echo "========================================"
echo "🚀 启动 Actress Report"
echo "========================================"

# 启动 FastAPI 后端
echo "[1/2] 启动 FastAPI backend (port 8765)..."
nohup uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765 > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "      ✓ Backend PID: $BACKEND_PID"

# 等待后端就绪
for i in {1..10}; do
    if curl -s --max-time 2 http://localhost:8765/api/health > /dev/null 2>&1; then
        echo "      ✓ Backend ready"
        break
    fi
    sleep 1
done

# 启动 Nuxt 前端
echo "[2/2] 启动 Nuxt frontend (port 3000)..."
cd frontend
nohup npm run dev -- --port 3000 > "../$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "      ✓ Frontend PID: $FRONTEND_PID"

echo ""
echo "========================================"
echo "✅ 服务已启动"
echo "========================================"
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8765"
echo "Health:   http://localhost:8765/api/health"
echo "Logs:     $LOG_DIR/"
echo ""
echo "停止命令:"
echo "  kill $BACKEND_PID $FRONTEND_PID"
echo "========================================"
