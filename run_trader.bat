@echo off
setlocal

echo Stopping any existing trading engines...
wmic process where "name='python.exe' and CommandLine like '%%dashboard_server.py%%'" call terminate >nul 2>&1
wmic process where "name='python.exe' and CommandLine like '%%breakout_scanner.py%%'" call terminate >nul 2>&1

timeout /t 1 /nobreak >nul

echo Starting Dashboard and Scanner in current terminal...
python dashboard_server.py

echo Trader Engine stopped.
endlocal
