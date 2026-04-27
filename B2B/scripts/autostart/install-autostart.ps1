# Registers three Scheduled Tasks (backend / bridge / cloudflared) that
# fire at the current user's logon — so a reboot brings the whole stack
# back up without touching anything. No admin rights required.
#
# Run from a normal PowerShell window:
#   powershell -ExecutionPolicy Bypass -File install-autostart.ps1
#
# Re-run any time — existing tasks are removed and recreated, idempotent.

$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$tasks = @(
    @{ Name = "BitCoding-Backend";     Script = "$dir\start-backend.bat";     Description = "FastAPI backend for the LinkedIn dashboard" },
    @{ Name = "BitCoding-Bridge";      Script = "$dir\start-bridge.bat";      Description = "Claude Bridge used by the drafter" },
    @{ Name = "BitCoding-Cloudflared"; Script = "$dir\start-cloudflared.bat"; Description = "Cloudflare Tunnel exposing the local backend on api.bitcodingsolutions.com" }
)

foreach ($t in $tasks) {
    if (-not (Test-Path $t.Script)) {
        throw "Missing script: $($t.Script)"
    }

    # Drop any earlier registration so re-runs are clean.
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$($t.Script)`""
    # Run at logon. -AtStartup needs admin; AtLogOn doesn't, and the user
    # logs in on every boot anyway, so this covers reboots.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # ExecutionTimeLimit 0 = no kill after N hours. Hidden window so the
    # console flash on logon is minimised.
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $t.Description | Out-Null
    Write-Host "Registered: $($t.Name)"
}

Write-Host ""
Write-Host "Done. Tasks fire on next logon. To start now without rebooting:"
foreach ($t in $tasks) {
    Write-Host "  Start-ScheduledTask -TaskName '$($t.Name)'"
}
Write-Host ""
Write-Host "Logs land in $dir\*.log"
Write-Host "Remove tasks any time with uninstall-autostart.ps1."
