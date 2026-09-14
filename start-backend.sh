#!/usr/bin/env bash
# AI Trading Agent — Start Backend (macOS / Linux)
# Mirrors start-backend.bat. Run from anywhere.
#
#   Backend:  http://localhost:8002
#   Docs:     http://localhost:8002/docs
#
# MT5 bridge: 后端通过 HTTP 调用 MT5 桥接服务（默认 http://localhost:8001）。
# MetaTrader5 Python 包仅支持 Windows，因此 bridge 必须跑在 Windows 主机上，
# 在 backend/.env 里把 MT5_BRIDGE_URL 指向它（局域网 IP 或 SSH 隧道到 localhost）。
# 详见 start-mt5bridge.sh。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
BACKEND_DIR="$PROJECT_DIR/backend"
PORT="${BACKEND_PORT:-8002}"
# 必须 export：MCP server 是独立子进程，靠 PORT/BACKEND_URL 回调 backend API
# （sentiment / P&L / history）。只作 shell 变量传给 uvicorn 的话子进程拿不到，
# 会回落到 tools.backend_url() 的默认端口而连不上。
export PORT

echo "========================================"
echo "  AI Trading Agent — Backend"
echo "  http://localhost:$PORT"
echo "========================================"

cd "$BACKEND_DIR"

# config.py 从 CWD 读取 .env，所以 backend/.env 必须存在
if [ ! -f ".env" ]; then
    echo "[ERROR] backend/.env 不存在。先创建："
    echo "        cp backend/.env.example backend/.env"
    echo "        然后填入 DATABASE_URL / DATABASE_URL_SYNC / MT5_BRIDGE_URL 等"
    exit 1
fi

# 与 .bat 一致：把 .env 里的 DATABASE_URL_SYNC 导出给 Alembic 迁移用
DATABASE_URL_SYNC="$(grep -E '^DATABASE_URL_SYNC=' .env | head -1 | cut -d= -f2- || true)"
if [ -z "$DATABASE_URL_SYNC" ]; then
    echo "[WARN] backend/.env 未设置 DATABASE_URL_SYNC — alembic 迁移将无法执行"
fi
export DATABASE_URL_SYNC
export PYTHONPATH="$BACKEND_DIR"

UVICORN="$BACKEND_DIR/.venv/bin/uvicorn"
if [ ! -x "$UVICORN" ]; then
    echo "[ERROR] 找不到虚拟环境 $UVICORN。先创建："
    echo "        python3 -m venv backend/.venv"
    echo "        backend/.venv/bin/pip install -r backend/requirements.txt"
    exit 1
fi

BRIDGE_URL="$(grep -E '^MT5_BRIDGE_URL=' .env | head -1 | cut -d= -f2- || true)"
echo
echo "  MT5 bridge: ${BRIDGE_URL:-http://localhost:8001（默认，请在 .env 设置 MT5_BRIDGE_URL）}"
echo

# 启动前自动应用数据库迁移，避免模型与 schema 漂移导致运行期 500
# （与 backend/Dockerfile CMD 的 alembic upgrade head 行为一致）。
# 缺少 DATABASE_URL_SYNC 或迁移失败时仅告警，不阻断启动。
ALEMBIC="$BACKEND_DIR/.venv/bin/alembic"
if [ -n "$DATABASE_URL_SYNC" ] && [ -x "$ALEMBIC" ]; then
    echo "[migration] alembic upgrade head"
    if ! "$ALEMBIC" upgrade head; then
        echo "[WARN] alembic upgrade head 失败 —— 若模型与数据库不一致，接口可能报 500"
    fi
else
    echo "[WARN] 跳过 alembic 迁移（DATABASE_URL_SYNC 为空或未找到 .venv/bin/alembic）"
fi

exec "$UVICORN" app.main:app --host 0.0.0.0 --port "$PORT"
