@echo off
REM Launches the Claude Bridge that the drafter calls into. Same pattern
REM as start-backend.bat — silent background launch on user logon.
cd /D "H:\Lead Generator\Bridge"
start "" /B python server.py >> "H:\Lead Generator\B2B\scripts\autostart\bridge.log" 2>&1
