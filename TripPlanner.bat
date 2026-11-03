@echo off
rem Double-click to open the trip planner in the browser. The first time, it creates the environment: that takes a few minutes.
rem The program runs in a small window of its own, started minimised: closing it, or the Quit button of the page, closes it.
cd /d "%~dp0"
if "%~1"=="run" goto :run

if not exist "env\.venv\Scripts\python.exe" (
    echo First run: creating the environment. This takes a few minutes, the browser opens when it is done.
    py -3.12 -m venv "env\.venv" 2>nul || python -m venv "env\.venv" || goto :failed
    "env\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-deps -r "env\requirements.lock" || goto :failed
)

if not exist "env\.env" (
    copy "env\.env.example" "env\.env" >nul
    echo The program needs two free API keys. Fill them in the file that opens now, save it and close it.
    notepad "env\.env"
)

start "Trip Planner" /min "%~f0" run
exit /b 0

:run
rem The program itself: its address is written here. An error that stops it stays on the screen until a key is pressed
"env\.venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo The trip planner stopped because of the error above.
    pause
)
exit /b

:failed
echo.
echo The environment could not be created. Python 3.12 must be installed: https://www.python.org/downloads/
pause
exit /b 1
