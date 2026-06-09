@echo off
chcp 65001 >nul
title Update IvyeaOps Windows x64
cd /d "%~dp0"
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\windows-action-gui.ps1" -Mode update
