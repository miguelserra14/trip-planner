@echo off
rem Double-click to open the discover map: where to fly cheaply from a city. It uses the planner's environment.
cd /d "%~dp0"

if not exist "env\.venv\Scripts\pythonw.exe" (
    echo The environment is not there yet: double-click TripPlanner.bat once first, it creates it.
    pause
    exit /b 1
)

start "" "env\.venv\Scripts\pythonw.exe" -m discover.window
exit /b 0
