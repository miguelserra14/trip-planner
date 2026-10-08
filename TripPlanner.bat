@echo off
rem Double-click to open the trip planner. The first time, it creates the environment: that takes a few minutes.
cd /d "%~dp0"

if not exist "env\.venv\Scripts\pythonw.exe" (
    echo First run: creating the environment. This takes a few minutes, the window opens when it is done.
    py -3.12 -m venv "env\.venv" 2>nul || python -m venv "env\.venv" || goto :failed
    "env\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-deps -r "env\requirements.lock" || goto :failed
)

if not exist "env\.env" (
    copy "env\.env.example" "env\.env" >nul
    echo The program needs two free API keys. Fill them in the file that opens now, save it and close it.
    notepad "env\.env"
)

start "" "env\.venv\Scripts\pythonw.exe" main.py
exit /b 0

:failed
echo.
echo The environment could not be created. Python 3.12 must be installed: https://www.python.org/downloads/
pause
exit /b 1
