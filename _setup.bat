@echo off
rem Общая подготовка: находит Python, при первом запуске создаёт окружение .venv и ставит библиотеки.
cd /d "%~dp0"
set "PYEXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYEXE%" goto deps
set "BOOT="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "BOOT=py -3"
if not defined BOOT python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "BOOT=python"
if not defined BOOT goto nopython
echo Первый запуск: создаю окружение Python (займёт около минуты)...
%BOOT% -m venv "%~dp0.venv"
if errorlevel 1 goto fail
:deps
fc /b "%~dp0requirements.txt" "%~dp0.venv\requirements.installed" >nul 2>&1
if not errorlevel 1 exit /b 0
echo Устанавливаю библиотеки (нужен интернет, только один раз)...
"%PYEXE%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt"
if errorlevel 1 goto fail
copy /y "%~dp0requirements.txt" "%~dp0.venv\requirements.installed" >nul
exit /b 0
:nopython
echo.
echo  Python не найден.
echo  1) Скачайте Python 3.12 или новее: https://www.python.org/downloads/
echo  2) При установке ОБЯЗАТЕЛЬНО отметьте галочку "Add python.exe to PATH".
echo  3) Запустите этот файл ещё раз.
start "" https://www.python.org/downloads/
exit /b 1
:fail
echo.
echo  Не удалось подготовить окружение. Проверьте интернет и запустите ещё раз.
echo  Если не помогло — удалите папку .venv рядом с программой и повторите.
exit /b 1
