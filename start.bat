@echo off
setlocal
cd /d "%~dp0"
echo Starting DeerFlow Unified System...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
