@echo off
chcp 65001 >nul
title AI Trading Agent - Build Frontend

set "PROJECT_DIR=C:\Users\liumou\workspace\ai-trading-agent"

echo ============================================
echo   Building frontend for production
echo ============================================
echo   Run this AFTER you change frontend code,
echo   then use start-all.bat to launch services.
echo ============================================
echo.

cd /d "%PROJECT_DIR%\frontend"

if not exist "node_modules" (
    echo [INFO] Installing dependencies...
    call "C:\Program Files\nodejs\npm.cmd" install
)

echo [INFO] Running: npm run build
call "C:\Program Files\nodejs\npm.cmd" run build

if %ERRORLEVEL% == 0 (
    echo.
    echo [OK] Build succeeded! .next is ready.
    echo      Now run start-all.bat
) else (
    echo.
    echo [ERROR] Build failed.
)
pause
