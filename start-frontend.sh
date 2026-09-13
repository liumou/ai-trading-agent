#!/usr/bin/env bash
# AI Trading Agent — Start Frontend (macOS / Linux)
# Mirrors start-frontend.bat. Production server on port 3000.
#
#   前端:  http://localhost:3000
#
# 注意：NEXT_PUBLIC_* 变量在构建时写死，所以 frontend/.env 必须在
# npm run build 之前就存在，且 API 地址要指向后端端口（默认 8002）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PORT="${FRONTEND_PORT:-3000}"
BACKEND_PORT="${BACKEND_PORT:-8002}"

echo "========================================"
echo "  AI Trading Agent — Frontend"
echo "  http://localhost:$PORT"
echo "========================================"

cd "$FRONTEND_DIR"

# NEXT_PUBLIC_* 在 build 时就地嵌入 —— 没有 .env 时先按后端 :8002 生成，
# 否则页面会去请求默认的 :8000 而连不上后端。
if [ ! -f ".env" ]; then
    echo "[INFO] 生成 frontend/.env，API 指向 http://localhost:$BACKEND_PORT ..."
    cat > .env <<EOF
NEXT_PUBLIC_API_URL=http://localhost:$BACKEND_PORT
NEXT_PUBLIC_WS_URL=ws://localhost:$BACKEND_PORT/ws
EOF
fi

if [ ! -d "node_modules" ]; then
    echo "[INFO] 安装依赖 (npm install)..."
    npm install
fi

if [ ! -f ".next/BUILD_ID" ]; then
    echo "[INFO] 未找到生产构建产物，开始构建 (npm run build)..."
    npm run build
fi

echo "[INFO] 启动生产服务器 (port $PORT)..."
exec npm run start -- -H 0.0.0.0 -p "$PORT"