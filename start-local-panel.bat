@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Follow README.md to install the backend environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\serve_local_panel.py start %*
set "PANEL_EXIT_CODE=%errorlevel%"
echo.
pause
exit /b %PANEL_EXIT_CODE%
