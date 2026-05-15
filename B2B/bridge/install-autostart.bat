@echo off
rem Install the bridge auto-start entry in the Windows Startup folder.
rem After running this once, the bridge will launch silently every time
rem you sign in to Windows. No console window.

setlocal
cd /d "%~dp0"

set "SRC=%CD%\start-silent.vbs"
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ClaudeBridge.vbs"

if not exist "%SRC%" (
    echo [ERROR] start-silent.vbs not found next to this batch file.
    pause
    exit /b 1
)

copy /Y "%SRC%" "%DST%" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy to Startup folder.
    echo         Try running this batch as Administrator.
    pause
    exit /b 1
)

echo.
echo [OK] Bridge auto-start installed.
echo.
echo      Source     : %SRC%
echo      Installed  : %DST%
echo.
echo The bridge will now launch silently at every Windows sign-in.
echo To uninstall: run uninstall-autostart.bat (or delete the file above).
echo.
echo Starting the bridge now so you don't have to sign out...
cscript //nologo "%SRC%"
echo.
echo Done. You can close this window.
pause
