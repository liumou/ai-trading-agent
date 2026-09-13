#!/usr/bin/env bash
# AI Trading Agent — MT5 Bridge helper
#
# MT5 桥接服务 (mt5_bridge/main.py) 依赖 MetaTrader5 Python 包 —— 该包仅支持
# Windows，并且需要与 MT5 终端 (terminal64.exe) 跑在同一台机器上。因此：
#
#   * 在 Windows 上运行本脚本（Git Bash / WSL 调用 Windows Python 亦可）→ 直接启动 bridge
#   * 在 macOS / Linux 上运行本脚本 → 不会启动 bridge，只做说明与连通性自检
#
# 本机（Mac）要“调用 MT5 服务”，有两种方式：
#   1) backend/.env 里设 MT5_BRIDGE_URL 指向 Windows 主机的局域网地址，例如
#         MT5_BRIDGE_URL=http://192.168.3.47:8001
#      同时把 MT5_BRIDGE_API_KEY 设成和 Windows 端一致。
#   2) 若 Windows 主机不在同一网段，用 SSH 隧道把远端 8001 映射到本机：
#         ssh -N -L 8001:localhost:8001 user@windows-host
#      然后保持 backend/.env 默认的 MT5_BRIDGE_URL=http://localhost:8001 即可。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
MT5_DIR="$PROJECT_DIR/mt5_bridge"
BRIDGE_PORT="${BRIDGE_PORT:-8001}"

resolve_bridge_url() {
    local url
    url="$(grep -E '^MT5_BRIDGE_URL=' "$PROJECT_DIR/backend/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    echo "${url:-http://localhost:$BRIDGE_PORT}"
}

check_reachable() {
    local url="$1"
    if curl -s -o /dev/null --max-time 3 "$url/health" 2>/dev/null; then
        echo "  [OK] MT5 Bridge 可连通: $url/health"
        return 0
    fi
    echo "  [!!] 无法连接 MT5 Bridge: $url/health"
    return 1
}

case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        # Windows 环境：真正启动 bridge
        echo "========================================"
        echo "  MT5 Bridge — http://localhost:$BRIDGE_PORT"
        echo "========================================"
        cd "$MT5_DIR"
        if [ ! -f ".env" ]; then
            echo "[ERROR] mt5_bridge/.env 不存在. 先: cp mt5_bridge/.env.example mt5_bridge/.env"
            exit 1
        fi
        UVICORN="$MT5_DIR/.venv/Scripts/uvicorn.exe"
        [ -x "$UVICORN" ] || UVICORN="$MT5_DIR/.venv/bin/uvicorn"
        if [ ! -x "$UVICORN" ]; then
            echo "[ERROR] 找不到 uvicorn，请先创建 mt5_bridge/.venv 并安装依赖："
            echo "        py -m venv mt5_bridge/.venv && mt5_bridge/.venv/Scripts/pip install -r mt5_bridge/requirements.txt"
            exit 1
        fi
        exec "$UVICORN" main:app --host 0.0.0.0 --port "$BRIDGE_PORT"
        ;;
    *)
        BRIDGE_URL="$(resolve_bridge_url)"
        echo "========================================"
        echo "  MT5 Bridge — 本机 $(uname -s) 无法运行"
        echo "========================================"
        echo
        echo "  MetaTrader5 Python 包仅支持 Windows，bridge 必须跑在装有"
        echo "  MT5 终端的 Windows 主机上（用 start-mt5bridge.bat 启动）。"
        echo
        echo "  当前 backend/.env 配置的桥接地址: $BRIDGE_URL"
        echo
        check_reachable "$BRIDGE_URL" || {
            echo
            echo "  排查建议："
            echo "    1) Windows 主机上确认 bridge 在监听，且防火墙放行 $BRIDGE_PORT"
            echo "    2) backend/.env 里 MT5_BRIDGE_URL 指向正确的 IP/端口"
            echo "    3) 跨网段时用 SSH 隧道：ssh -N -L $BRIDGE_PORT:localhost:$BRIDGE_PORT user@windows-host"
            echo "    4) MT5_BRIDGE_API_KEY 需与 Windows 端一致，否则业务接口返回 401"
        }
        echo
        echo "  语法检查:  bash -n \"$0\""
        exit 0
        ;;
esac
