@echo off
chcp 65001 >nul
title AI Trading Agent - All Services

set "PROJECT_DIR=C:\Users\liumou\workspace\ai-trading-agent"

echo ========================================
echo   AI Trading Agent - All Services
echo ========================================
echo.
echo   Frontend:     http://localhost:3000
echo   Backend:      http://localhost:8002
echo   MT5 Bridge:   http://localhost:8001
echo.
echo   LAN:
echo   Frontend:     http://192.168.3.47:3000
echo   Backend:      http://192.168.3.47:8002
echo   MT5 Bridge:   http://192.168.3.47:8001
echo.

echo [1/4] Stopping old processes...
taskkill /F /IM uvicorn.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1
taskkill /F /IM terminal64.exe >nul 2>&1
ping 127.0.0.1 -n 3 >nul

echo [2/4] Starting Backend (port 8002)...
start "Backend" /D "%PROJECT_DIR%" cmd /c ""%PROJECT_DIR%\start-backend.bat""
ping 127.0.0.1 -n 9 >nul

echo [3/4] Starting MT5 Bridge (port 8001)...
start "MT5Bridge" /D "%PROJECT_DIR%" cmd /c ""%PROJECT_DIR%\start-mt5bridge.bat""
ping 127.0.0.1 -n 9 >nul

echo [4/4] Starting Frontend (port 3000)...
start "Frontend" /D "%PROJECT_DIR%" cmd /c ""%PROJECT_DIR%\start-frontend.bat""
ping 127.0.0.1 -n 11 >nul

echo.
echo ========================================
echo   All services started!
echo ========================================
echo.
echo   Checking ports...
for /L %%i in (1,1,3) do (
    netstat -ano | findstr ":3000" | findstr "LISTENING" >nul && echo   [OK] Frontend  :3000 || echo   [..] Frontend still starting
    netstat -ano | findstr ":8002" | findstr "LISTENING" >nul && echo   [OK] Backend   :8002 || echo   [..] Backend still starting
    netstat -ano | findstr ":8001" | findstr "LISTENING" >nul && echo   [OK] MT5 Bridge:8001 || echo   [..] MT5 Bridge still starting
    ping 127.0.0.1 -n 4 >nul
)
echo.
pause
