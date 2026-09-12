@echo off
chcp 65001 >nul
title AI Trading Agent - MT5 Bridge

set "PROJECT_DIR=C:\Users\liumou\workspace\ai-trading-agent"
set "MT5_DIR=%PROJECT_DIR%\mt5_bridge"

echo ========================================
echo   MT5 Bridge - http://localhost:8001
echo ========================================

cd /d "%MT5_DIR%"
"%MT5_DIR%\.venv\Scripts\uvicorn.exe" main:app --host 0.0.0.0 --port 8001
