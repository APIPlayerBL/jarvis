@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Не найден .venv. Создай окружение: py -3.12 -m venv .venv
    pause
    exit /b 1
)
start "Jarvis" ".venv\Scripts\pythonw.exe" gui.py %*
