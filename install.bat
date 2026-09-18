@echo off
setlocal
cd /d "%~dp0"
echo Starting Agent Workspace Installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Installation failed. See above messages.
    pause
    exit /b %ERRORLEVEL%
)
pause
