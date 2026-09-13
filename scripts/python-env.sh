#!/usr/bin/env bash
# AI Trading Agent — shared Python environment bootstrap.
# Source this file, then call ensure_python_env:
#
#   source "$SCRIPT_DIR/scripts/python-env.sh"
#   ensure_python_env <app_dir> <requirements.txt 相对路径> [<uvicorn 脚本名>] [module ...]
#
# 自动解决常见的 Python 环境问题（不依赖用户手动建 venv / 装依赖）：
#   * 没有 .venv                -> 自动创建（优先 uv，其次 python3 -m venv）
#   * venv 由 uv 创建、没有 pip -> 优先用 `uv pip install`；否则 ensurepip 引导 pip
#   * 依赖缺失 / 过期            -> requirements.txt 哈希变化时自动重装
#   * PATH 上 python 版本不对    -> 一律显式使用 venv 内解释器
# 成功后导出：VENV_DIR / VENV_PYTHON / VENV_UVICORN（调用方 exec 用）。

set -uo pipefail

ensure_python_env() {
    local app_dir="$1"; shift
    local req_rel="$1"; shift
    local uvicorn_rel=""
    if [ "$#" -gt 0 ]; then
        uvicorn_rel="$1"; shift
    fi

    VENV_DIR="$app_dir/.venv"
    REQ_FILE="$app_dir/$req_rel"
    VENV_PYTHON=""
    VENV_UVICORN=""

    if [ ! -f "$REQ_FILE" ]; then
        echo "[ERROR] 找不到依赖清单: $REQ_FILE"
        exit 1
    fi

    # ---- 找一个可用的 Python（uv 能自动托管解释器，优先） ----
    local UV=""
    for cand in "$(command -v uv 2>/dev/null || true)" "$HOME/.local/bin/uv" "/usr/local/bin/uv" "/opt/homebrew/bin/uv"; do
        [ -n "$cand" ] && [ -x "$cand" ] && UV="$cand" && break
    done
    local PY_CMD=""
    if [ -n "$UV" ]; then
        PY_CMD="uv"
    elif command -v python3.12 >/dev/null 2>&1; then
        PY_CMD="python3.12"
    elif command -v python3.11 >/dev/null 2>&1; then
        PY_CMD="python3.11"
    elif command -v python3 >/dev/null 2>&1; then
        PY_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PY_CMD="python"
    else
        echo "[ERROR] 找不到 Python。请安装 Python 3.11+（推荐装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh）"
        exit 1
    fi

    # ---- 创建 venv（缺失时） ----
    if [ ! -x "$VENV_DIR/bin/python" ] && [ ! -x "$VENV_DIR/Scripts/python.exe" ]; then
        echo "[INFO] 创建虚拟环境: $VENV_DIR ..."
        if [ "$PY_CMD" = "uv" ]; then
            "$UV" venv --python 3.12 "$VENV_DIR" >/dev/null
        else
            "$PY_CMD" -m venv "$VENV_DIR"
        fi
        if [ ! -x "$VENV_DIR/bin/python" ] && [ ! -x "$VENV_DIR/Scripts/python.exe" ]; then
            echo "[ERROR] 虚拟环境创建失败: $VENV_DIR 中没有 python"
            exit 1
        fi
    fi

    VENV_PYTHON="$VENV_DIR/bin/python"
    [ -x "$VENV_PYTHON" ] || VENV_PYTHON="$VENV_DIR/Scripts/python.exe"

    # ---- 计算 requirements.txt 指纹，决定是否重装依赖 ----
    local stamp="$VENV_DIR/.requirements.sha256"
    local want
    want="$("$VENV_PYTHON" -c "import hashlib;print(hashlib.sha256(open(r'$REQ_FILE','rb').read()).hexdigest())" 2>/dev/null || echo never)"

    _all_imports_ok() {
        [ "$#" -eq 0 ] && return 0
        local mods=""
        local m
        for m in "$@"; do mods="${mods:+$mods, }import $m"; done
        "$VENV_PYTHON" -c "$mods" >/dev/null 2>&1
    }

    if [ ! -f "$stamp" ] || [ "$(cat "$stamp" 2>/dev/null || true)" != "$want" ]; then
        if _all_imports_ok "$@"; then
            # 依赖其实已装好（比如 venv 是拷来的），直接打标跳过重装
            printf '%s' "$want" > "$stamp"
        else
            echo "[INFO] 安装/更新依赖（requirements.txt 已变化或首次安装）..."
            if [ -n "$UV" ]; then
                if ! "$UV" pip install --python "$VENV_PYTHON" -r "$REQ_FILE"; then
                    echo "[ERROR] 依赖安装失败（uv pip）。请检查网络后重试。"
                    exit 1
                fi
            else
                # uv 建的 venv 默认没有 pip，ensurepip 引导一下
                "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
                if ! "$VENV_PYTHON" -m pip install -r "$REQ_FILE"; then
                    echo "[ERROR] 依赖安装失败（pip）。请检查网络后重试。"
                    exit 1
                fi
            fi
            printf '%s' "$want" > "$stamp"
        fi
    fi

    # ---- 导入自检：装完之后核心模块必须能 import ----
    if ! _all_imports_ok "$@"; then
        echo "[ERROR] 虚拟环境不完整：以下依赖导入失败: $*"
        echo "        可删除 $stamp 后重跑本脚本强制重装。"
        exit 1
    fi

    # ---- 定位 uvicorn 可执行文件（Windows 在 Scripts/，POSIX 在 bin/） ----
    if [ -n "$uvicorn_rel" ]; then
        local cand
        for cand in "$VENV_DIR/Scripts/$uvicorn_rel" "$VENV_DIR/bin/$uvicorn_rel"; do
            if [ -x "$cand" ]; then
                VENV_UVICORN="$cand"
                break
            fi
        done
        if [ -z "$VENV_UVICORN" ]; then
            echo "[ERROR] 虚拟环境里找不到 uvicorn（$uvicorn_rel）"
            exit 1
        fi
    fi
}
