#!/usr/bin/env bash
# AI Trading Agent — Stop All Services (macOS / Linux)
# Mirrors stop-all.bat.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
PORTS=(8002 3000 8001)   # backend / frontend / 本地 MT5 桥接（含 SSH 隧道）

echo "Stopping all services..."

# 1) 先按 start-all.sh 记录的 PID 关闭
for name in backend frontend; do
    f="$LOG_DIR/$name.pid"
    [ -f "$f" ] || continue
    pid="$(cat "$f" 2>/dev/null || true)"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        echo "  killed PID $pid ($name)"
    fi
    rm -f "$f"
done

# 2) 再按端口兜底（能清掉 npm 派生的 next-server 子进程）
#    只匹配 LISTEN 状态，避免误杀连到远端 :8002/192.168.3.47 的浏览器等客户端连接
for port in "${PORTS[@]}"; do
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
    if [ -n "${pids// /}" ]; then
        echo "  port $port (LISTEN) → $pids"
        lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
        sleep 1
        lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null || true
    fi
done

echo "Done."
