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

# 前端运行时自动推导后端地址（lib/api.ts / lib/websocket.ts）：
#   协议://<访问前端的主机>:<BACKEND_PORT>
# 本机(localhost)、局域网任意 IP、Tailscale IP 均自动连到同一台机器的后端。
# 因此无需在此写死 IP/localhost；仅当后端不在 :8002 或需公网覆盖时才取消注释。
if [ ! -f ".env" ]; then
    echo "[INFO] 生成 frontend/.env（空配置，API 地址运行时自动推导）..."
    cat > .env <<EOF
# 运行时自动推导后端地址（见 lib/api.ts）。后端端口默认 $BACKEND_PORT。
# NEXT_PUBLIC_BACKEND_PORT=$BACKEND_PORT
# 如需显式覆盖（公网部署）：
# NEXT_PUBLIC_API_URL=http://localhost:$BACKEND_PORT
# NEXT_PUBLIC_WS_URL=ws://localhost:$BACKEND_PORT/ws
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