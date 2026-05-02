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
# Tasks split by trigger type: "logon" runs at sign-in (long-lived
# services); "daily" runs once a day at a specified time (maintenance).
$tasks = @(
    @{ Name = "BitCoding-Backend";     Script = "$dir\start-backend.bat";     Trigger = "logon"; Description = "FastAPI backend for the LinkedIn dashboard" },
    @{ Name = "BitCoding-Bridge";      Script = "$dir\start-bridge.bat";      Trigger = "logon"; Description = "Claude Bridge used by the drafter" },
    @{ Name = "BitCoding-Cloudflared"; Script = "$dir\start-cloudflared.bat"; Trigger = "logon"; Description = "Cloudflare Tunnel exposing the local backend on api.bitcodingsolutions.com" },
    # 03:15 chosen because it's after midnight (clean date boundary) and
    # before any human is likely to be using the dashboard.
    @{ Name = "BitCoding-DBBackup";    Script = "$dir\backup-db.bat";         Trigger = "daily"; DailyAt = "03:15"; Description = "Daily SQLite snapshot of the LinkedIn dashboard DB (14-day retention)" }
)

foreach ($t in $tasks) {
    if (-not (Test-Path $t.Script)) {
        throw "Missing script: $($t.Script)"
    }

    # Drop any earlier registration so re-runs are clean.
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$($t.Script)`""
    if ($t.Trigger -eq "daily") {
        # Daily at fixed local time. -DaysInterval 1 is the default; spelled
        # out for clarity.
        $trigger = New-ScheduledTaskTrigger -Daily -At $t.DailyAt -DaysInterval 1
    } else {
        # Run at logon. -AtStartup needs admin; AtLogOn doesn't, and the
        # user logs in on every boot anyway, so this covers reboots.
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    }
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
