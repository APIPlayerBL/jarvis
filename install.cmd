@echo off
setlocal
cd /d "%~dp0"
echo Creating Python environment...
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv || python -m venv .venv
)
echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Environment is ready. Launch: run_jarvis.cmd
pause
