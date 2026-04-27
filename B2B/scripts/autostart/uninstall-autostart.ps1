# Drops the three Scheduled Tasks registered by install-autostart.ps1.
# Use to disable auto-start without uninstalling anything else. Idempotent.

$ErrorActionPreference = "Continue"

foreach ($name in @("BitCoding-Backend", "BitCoding-Bridge", "BitCoding-Cloudflared")) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed: $name (or already absent)"
}
