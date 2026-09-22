# Alpha Autostart Unregistration
# Removes the Alpha_Autostart and Alpha_Watchdog scheduled tasks.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\unregister_autostart.ps1

$ErrorActionPreference = "SilentlyContinue"

Write-Host "`nRemoving Alpha autostart scheduled tasks..." -ForegroundColor Yellow

foreach ($name in @("Alpha_Autostart", "Alpha_Watchdog")) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "  [OK] Removed: $name" -ForegroundColor Green
    } else {
        Write-Host "  [--] Not found: $name (already removed)" -ForegroundColor Gray
    }
}

# Also stop the watchdog loop if running
$RepoRoot = Split-Path $PSScriptRoot -Parent
$WatchdogPid = "$RepoRoot\logs\watchdog.pid"
if (Test-Path $WatchdogPid) {
    $wdPid = [int](Get-Content $WatchdogPid -Raw -ErrorAction SilentlyContinue)
    if ($wdPid -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) {
        Write-Host "  Stopping watchdog loop (PID $wdPid)..." -ForegroundColor Gray
        & taskkill /PID $wdPid /T /F 2>$null | Out-Null
    }
    Remove-Item $WatchdogPid -Force -ErrorAction SilentlyContinue
}

Write-Host "`n[OK] Alpha autostart removed. Alpha will no longer start automatically.`n" -ForegroundColor Green
Write-Host "To re-enable: .\scripts\register_autostart.ps1`n" -ForegroundColor Gray
