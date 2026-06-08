@echo off
chcp 65001 >nul
title Stop IvyeaOps
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-hidden.ps1"
echo.
pause
