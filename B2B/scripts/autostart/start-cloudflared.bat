@echo off
REM Launches the Cloudflare tunnel that exposes the local backend on
REM api.bitcodingsolutions.com. Run as a Scheduled Task at user logon —
REM no admin needed because we're running it as the logged-in user, NOT
REM installing a Windows service.
start "" /B "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --config "C:\Users\Pradip Kachhadiya\.cloudflared\config.yml" run c20cd640-cf74-4862-ac13-355405b86fb7 >> "H:\Lead Generator\B2B\scripts\autostart\cloudflared.log" 2>&1
