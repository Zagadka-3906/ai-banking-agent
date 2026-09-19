@echo off
title AI Banking Agent - Quick Start
echo ===================================================
echo   AI Banking Agent - Quick Start
echo ===================================================
cd /d "%~dp0"

echo [1/3] Checking backend services (ports 8000, 8100)...
netstat -ano | findstr 8000 >nul
if %errorlevel% neq 0 (
    echo   Starting Core API on port 8000...
    start /min "" ".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
) else (
    echo   Core API is already running on port 8000.
)

netstat -ano | findstr 8100 >nul
if %errorlevel% neq 0 (
    echo   Starting Gateway API on port 8100...
    start /min "" ".venv\Scripts\python.exe" -m uvicorn agent_api:app --host 0.0.0.0 --port 8100
) else (
    echo   Gateway API is already running on port 8100.
)

echo [2/3] Launching Android Emulator (bankagent)...
start "" "D:\AndroidDev\sdk\emulator\emulator.exe" -avd bankagent

echo [3/3] Waiting for emulator to be ready and launching app...
"D:\AndroidDev\sdk\platform-tools\adb.exe" wait-for-device
timeout /t 5 /nobreak >nul
"D:\AndroidDev\sdk\platform-tools\adb.exe" shell wm dismiss-keyguard
"D:\AndroidDev\sdk\platform-tools\adb.exe" shell am start -n com.aibanking.agent/.MainActivity

echo.
echo ===================================================
echo   All services and App started!
echo ===================================================
pause