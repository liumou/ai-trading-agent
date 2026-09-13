#!/usr/bin/env bash
# AI Trading Agent — Start All Services (macOS / Linux)
# Mirrors start-all.bat.
#
#   Frontend:   http://localhost:3000
#   Backend:    http://localhost:8002
#   MT5 bridge: 跑在 Windows 主机上（MetaTrader5 仅 Windows），
#               通过 backend/.env 的 MT5_BRIDGE_URL 指定；本脚本会探测其连通性。
#
# 用法:
#   ./start-all.sh                      # 后台启动，日志写入 logs/
#   MT5_BRIDGE_URL=http://192.168.3.47:8001 ./start-all.sh
#   BACKEND_PORT=8002 FRONTEND_PORT=3000 ./start-all.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

BACKEND_PORT="${BACKEND_PORT:-8002}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# MT5 桥接地址：优先取环境变量，其次读 backend/.env，最后用默认值
BRIDGE_URL="${MT5_BRIDGE_URL:-}"
if [ -z "$BRIDGE_URL" ]; then
    BRIDGE_URL="$(grep -E '^MT5_BRIDGE_URL=' "$PROJECT_DIR/backend/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
fi
BRIDGE_URL="${BRIDGE_URL:-http://localhost:8001}"

echo "========================================"
echo "  AI Trading Agent — All Services"
echo "========================================"
echo "  Frontend:   http://localhost:$FRONTEND_PORT"
echo "  Backend:    http://localhost:$BACKEND_PORT"
echo "  MT5 bridge: $BRIDGE_URL"
echo "  Logs:       $LOG_DIR/{backend,frontend}.log"
echo

echo "[1/3] 停止旧进程..."
"$SCRIPT_DIR/stop-all.sh" >/dev/null 2>&1 || true
sleep 2

echo "[2/3] 启动服务（后台）..."
"$SCRIPT_DIR/start-backend.sh"  >"$LOG_DIR/backend.log"  2>&1 &
echo $! > "$LOG_DIR/backend.pid"

"$SCRIPT_DIR/start-frontend.sh" >"$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$LOG_DIR/frontend.pid"

# 连接成功即视为就绪（不校验 HTTP 状态码，避免 /health 降级时误报）
up() { curl -s -o /dev/null --max-time 2 "$1" 2>/dev/null; }

echo "[3/3] 等待服务就绪..."
ok_be=0; ok_fe=0
for _ in $(seq 1 40); do
    [ "$ok_be" -eq 0 ] && up "http://localhost:$BACKEND_PORT/health"  && ok_be=1
    [ "$ok_fe" -eq 0 ] && up "http://localhost:$FRONTEND_PORT"        && ok_fe=1
    { [ "$ok_be" -eq 1 ] && [ "$ok_fe" -eq 1 ]; } && break
    sleep 1
done

echo
echo "========================================"
[ "$ok_be" -eq 1 ] && echo "  [OK] Backend  :$BACKEND_PORT"  || echo "  [..] Backend  :$BACKEND_PORT 未就绪（见 logs/backend.log）"
[ "$ok_fe" -eq 1 ] && echo "  [OK] Frontend :$FRONTEND_PORT" || echo "  [..] Frontend :$FRONTEND_PORT 未就绪（见 logs/frontend.log）"

if up "$BRIDGE_URL/health"; then
    echo "  [OK] MT5 Bridge $BRIDGE_URL 已连通"
else
    echo "  [!!] MT5 Bridge $BRIDGE_URL 无法连接"
    echo "       bridge 必须运行在装有 MT5 终端的 Windows 主机上；"
    echo "       请在 backend/.env 设置 MT5_BRIDGE_URL 指向它。详见 ./start-mt5bridge.sh"
fi

echo "========================================"
echo
echo "  查看日志:  tail -f logs/backend.log logs/frontend.log"
echo "  停止服务:  ./stop-all.sh"
echo
