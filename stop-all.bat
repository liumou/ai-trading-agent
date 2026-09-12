@echo off
chcp 65001 >nul
title AI Trading Agent - Stop All

echo Stopping all services...
taskkill /F /IM uvicorn.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1
taskkill /F /IM terminal64.exe >nul 2>&1
echo Done.
ping 127.0.0.1 -n 3 >nul
