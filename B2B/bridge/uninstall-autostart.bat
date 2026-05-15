@echo off
rem Remove the bridge auto-start entry from the Windows Startup folder.

set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ClaudeBridge.vbs"

if exist "%DST%" (
    del "%DST%"
    echo [OK] Auto-start entry removed.
    echo      The bridge will no longer launch at sign-in.
) else (
    echo [INFO] No auto-start entry found at:
    echo        %DST%
)
echo.
echo Note: this does NOT stop a currently running bridge.
echo To stop it right now, run stop-bridge.bat.
pause
