@echo off
setlocal
cd /d "%~dp0"
echo Starting Alpha Watchdog (background monitor)...
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\watchdog.ps1" %*
