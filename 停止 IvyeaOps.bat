@echo off
chcp 65001 >nul
title Stop IvyeaOps
cd /d "%~dp0"
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\windows-action-gui.ps1" -Mode stop
