@echo off
chcp 65001 >nul
title Moodle Offline - быстрая синхронизация
call "%~dp0_setup.bat" || (pause & exit /b 1)
"%~dp0.venv\Scripts\python.exe" "%~dp0moodle_offline.py" --sync %*
echo.
pause
