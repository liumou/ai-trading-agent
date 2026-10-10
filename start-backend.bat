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

REM Read DATABASE_URL_SYNC from .env so credentials are not hardcoded here.
REM Use findstr to isolate the line before splitting on '='.
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b "DATABASE_URL_SYNC=" .env`) do set "DATABASE_URL_SYNC=%%b"

if not defined DATABASE_URL_SYNC (
    echo [ERROR] DATABASE_URL_SYNC not found in .env
    pause
    exit /b 1
)

REM Apply pending Alembic migrations before startup (matches backend/Dockerfile CMD).
REM Failure only warns - does not block startup.
echo [migration] alembic upgrade head
"%BACKEND_DIR%\.venv\Scripts\alembic.exe" upgrade head
if errorlevel 1 echo [WARN] alembic upgrade failed - API may error 500 if schema drifted

echo [INFO] Starting uvicorn on port 8002...
"%BACKEND_DIR%\.venv\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port 8002