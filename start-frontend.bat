@echo off
chcp 65001 >nul
title AI Trading Agent - Frontend (Production)

set "PROJECT_DIR=C:\Users\liumou\workspace\ai-trading-agent"
set "FRONTEND_DIR=%PROJECT_DIR%\frontend"

echo ========================================
echo   Frontend - http://localhost:3000
echo ========================================

cd /d "%FRONTEND_DIR%"

if not exist "node_modules" (
    echo [INFO] Installing dependencies...
    call "C:\Program Files\nodejs\npm.cmd" install
)

if not exist ".next\BUILD_ID" (
    echo [INFO] Production build not found. Building...
    call "C:\Program Files\nodejs\npm.cmd" run build
)

echo [INFO] Starting production server (port 3000)...
call "C:\Program Files\nodejs\npm.cmd" run start -- -H 0.0.0.0 -p 3000
