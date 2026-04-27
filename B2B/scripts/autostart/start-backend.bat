@echo off
REM Launches the FastAPI backend used by the LinkedIn dashboard. Designed
REM to run from a Scheduled Task at user logon. The /B in start makes it
REM run in the background (no console window).
cd /D "H:\Lead Generator\B2B\dashboard\backend"
start "" /B python -m uvicorn main:app --host 0.0.0.0 --port 8900 >> "H:\Lead Generator\B2B\scripts\autostart\backend.log" 2>&1
