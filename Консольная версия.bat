@echo off
chcp 65001 >nul
title Moodle Offline - консоль
call "%~dp0_setup.bat" || (pause & exit /b 1)
"%~dp0.venv\Scripts\python.exe" "%~dp0moodle_offline.py" %*
echo.
pause
