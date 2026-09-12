@echo off
chcp 65001 >nul
title AI Trading Agent - Backend

set "PROJECT_DIR=C:\Users\liumou\workspace\ai-trading-agent"
set "BACKEND_DIR=%PROJECT_DIR%\backend"

echo ========================================
echo   Backend - http://localhost:8002
echo ========================================

cd /d "%BACKEND_DIR%"
set "PYTHONPATH=%BACKEND_DIR%"

REM 从 .env 读取 DATABASE_URL_SYNC，避免密码硬编码在脚本中
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if "%%a"=="DATABASE_URL_SYNC" set "DATABASE_URL_SYNC=%%b"
)
if not defined DATABASE_URL_SYNC (
    echo [ERROR] DATABASE_URL_SYNC not found in .env
    pause
    exit /b 1
)

"%BACKEND_DIR%\.venv\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port 8002
