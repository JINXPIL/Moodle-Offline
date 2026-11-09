@echo off
chcp 65001 >nul
title Moodle Offline - сборка .exe
call "%~dp0_setup.bat" || (pause & exit /b 1)
echo Ставлю PyInstaller...
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pyinstaller || (pause & exit /b 1)
echo Собираю один файл MoodleOffline.exe (2-3 минуты)...
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name MoodleOffline ^
  --add-data "%~dp0ui;ui" --distpath "%~dp0dist" --workpath "%~dp0build" --specpath "%~dp0build" "%~dp0gui.py"
if errorlevel 1 (echo Сборка не удалась. & pause & exit /b 1)
echo.
echo Готово: %~dp0dist\MoodleOffline.exe
echo Этот файл можно скопировать на флешку и запускать на любом Windows 10/11 без установки Python.
explorer "%~dp0dist"
pause
