@echo off
setlocal
title AI Banking Agent - Quick Start
echo ===================================================
echo   AI Banking Agent - Quick Start
echo ===================================================
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0android\tools\start-emulator.ps1" %*
set "START_EXIT_CODE=%errorlevel%"
echo.
pause
exit /b %START_EXIT_CODE%
