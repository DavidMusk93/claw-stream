#!/bin/bash
# run.sh — 启动 Star Archive 完整服务（开发模式，自动重启 + 热重载）
#
# 用法: ./scripts/run.sh
# 特性:
#   - FastAPI backend (port 8765)  — 代码改动自动 reload
#   - Nuxt frontend (port 3000)    — HMR 热更新
#   - 任一服务崩溃后自动重启
#   - Ctrl+C 一次停止全部

set -uo pipefail

cd "$(dirname "$0")/.."

export LOG_DIR="${LOG_DIR:-$(pwd)/logs}"
mkdir -p "$LOG_DIR"

BACKEND_PID=""
FRONTEND_PID=""

stop_all() {
    echo ""
    echo "🛑 停止服务..."
    if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null
        wait "$BACKEND_PID" 2>/dev/null
    fi
    if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        kill "$FRONTEND_PID" 2>/dev/null
        wait "$FRONTEND_PID" 2>/dev/null
    fi
    echo "✅ 已停止"
    exit 0
}

trap stop_all SIGINT SIGTERM

start_backend() {
    echo "[backend] 启动 FastAPI (port 8765, reload enabled)..."
    PYTHONPATH="$(pwd)" uv run python -m uvicorn backend.main:app \
        --host 0.0.0.0 --port 8765 \
        --reload --reload-dir backend --reload-dir core \
        >> "$LOG_DIR/backend.log" 2>&1 &
    BACKEND_PID=$!
}

start_frontend() {
    echo "[frontend] 启动 Nuxt dev (port 3000)..."
    cd frontend
    npm run dev -- --port 3000 >> "../$LOG_DIR/frontend.log" 2>&1 &
    FRONTEND_PID=$!
    cd ..
}

wait_backend_ready() {
    for i in {1..15}; do
        if curl -s --max-time 2 http://localhost:8765/api/health > /dev/null 2>&1; then
            echo "[backend] 就绪"
            return 0
        fi
        sleep 1
    done
    echo "[backend] 等待超时，继续..."
}

echo "========================================"
echo "🚀 启动 Star Archive（开发模式）"
echo "========================================"

start_backend
wait_backend_ready
start_frontend

echo ""
echo "========================================"
echo "✅ 服务运行中"
echo "========================================"
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8765"
echo "Health:   http://localhost:8765/api/health"
echo "Logs:     $LOG_DIR/"
echo ""
echo "按 Ctrl+C 停止"
echo "========================================"

# 守护循环：任一进程退出则自动重启
while true; do
    if [[ -n "$BACKEND_PID" ]] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "[backend] 进程退出，自动重启..."
        start_backend
        wait_backend_ready
    fi
    if [[ -n "$FRONTEND_PID" ]] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo "[frontend] 进程退出，自动重启..."
        start_frontend
    fi
    sleep 3
done
