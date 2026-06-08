@echo off
chcp 65001 >nul
title Install IvyeaOps Windows x64
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-exe.ps1"
pause
