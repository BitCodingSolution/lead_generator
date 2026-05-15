@echo off
rem Stop any bridge process currently listening on port 8766.

echo Looking for bridge process on port 8766...

set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8766" ^| findstr "LISTENING"') do (
    set "FOUND=1"
    echo Killing PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo    [ERROR] Could not kill PID %%a. Try running as Administrator.
    ) else (
        echo    [OK] Killed.
    )
)

if not defined FOUND (
    echo [INFO] No bridge is currently running on port 8766.
)

echo.
pause
