@echo off
setlocal enabledelayedexpansion
title Trader Pilot - Integrated Web UI & Scanner Engine

echo ======================================================================
echo   TRADER PILOT & UMX VYUHA - INTEGRATED SERVICE LAUNCHER
echo ======================================================================
echo.

:: 1. Read command-line arguments or prompt user
set PORT=%1
set HOST=%2

if "%PORT%"=="" (
    set /p PORT="Enter Port number [Press Enter for default 8080]: "
    if "!PORT!"=="" set PORT=8080
)

if "%HOST%"=="" (
    set /p HOST="Enter Host address [Press Enter for default 0.0.0.0]: "
    if "!HOST!"=="" set HOST=0.0.0.0
)

:: 2. Check virtual environment
if exist .venv\Scripts\activate.bat (
    echo Activating .venv virtual environment...
    call .venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    echo Activating venv virtual environment...
    call venv\Scripts\activate.bat
)

:: 3. Terminate any orphaned dashboard or scanner processes
echo Stopping any running dashboard instances on port !PORT!...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":!PORT! " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)

:: 4. Launch integrated Web UI & Scanner background thread
echo.
echo ======================================================================
echo   Launching Web UI and Background Scanner Engine...
echo   Binding to: !HOST!:!PORT!
echo ======================================================================
echo.

python dashboard_server.py --host !HOST! --port !PORT!

echo.
echo [INFO] Trader Pilot process terminated.
pause
endlocal
