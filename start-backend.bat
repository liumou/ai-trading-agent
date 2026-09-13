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

REM 启动前自动应用数据库迁移，避免模型与 schema 漂移导致运行期 500
REM （与 backend/Dockerfile CMD 的 alembic upgrade head 行为一致）；失败仅告警，不阻断启动。
echo [migration] alembic upgrade head
"%BACKEND_DIR%\.venv\Scripts\alembic.exe" upgrade head
if errorlevel 1 echo [WARN] alembic upgrade head 失败 - 若模型与数据库不一致，接口可能报 500

"%BACKEND_DIR%\.venv\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port 8002
