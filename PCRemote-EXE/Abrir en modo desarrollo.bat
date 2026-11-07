@echo off
chcp 65001 >nul
cd /d "%~dp0app"
..\..\.venv\Scripts\python.exe server.py
pause
