# Alpha - Uninstaller for the autonomous/always-on layer
# Usage:
#   .\uninstall.ps1                 # stop Alpha, remove autostart + watchdog + state
#   .\uninstall.ps1 -PurgeLogs      # also delete logs\* and generated shim files
#   .\uninstall.ps1 -Yes            # non-interactive (no confirmation prompt)
#
# This removes the autonomous operation layer ONLY:
#   - stops services and the watchdog (maintenance mode)
#   - removes the Alpha_Autostart / Alpha_Watchdog scheduled tasks
#   - removes generated launchers/shims and PID/health/heartbeat state
#   - verifies that no Alpha watchdog or stack process remains
# Source code, .env, config.yaml and the virtualenv are left untouched; delete
# the folder yourself once this exits cleanly.

[CmdletBinding()]
param(
    [switch]$PurgeLogs,
    [switch]$Yes
)

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = $PSScriptRoot
$LogDir   = "$RepoRoot\logs"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "    Alpha - Uninstall autonomous operation layer" -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

if (-not $Yes) {
    $answer = Read-Host "This stops Alpha and removes autostart/watchdog. Continue? [y/N]"
    if ($answer -notmatch '^[Yy]') { Write-Host "Aborted."; exit 0 }
}

$script:Results = New-Object System.Collections.ArrayList
function Add-Result {
    param([string]$Name, [bool]$Ok, [string]$Detail = "")
    $status = if ($Ok) { "PASS" } else { "FAIL" }
    $color  = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1}{2}" -f $status, $Name, $(if ($Detail) { " - $Detail" } else { "" })) -ForegroundColor $color
    [void]$script:Results.Add([pscustomobject]@{ Check = $Name; Result = $status; Detail = $Detail })
}

# ---- 1. Stop everything through the sanctioned path (maintenance flag) ------
Write-Host "Stopping Alpha (maintenance mode)..." -ForegroundColor Yellow
if (Test-Path "$RepoRoot\stop.ps1") {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$RepoRoot\stop.ps1" 2>&1 | Out-Null
}

# Belt-and-braces: stop.sh-style sweeps so uninstall cannot be blocked by a
# hung component (this is the one place a broad sweep is correct).
$ports = @(8001, 3000, 8201, 2026)
foreach ($port in $ports) {
    try {
        foreach ($c in (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
            if ($c.OwningProcess -and $c.OwningProcess -ne 0) {
                & taskkill /PID $c.OwningProcess /T /F 2>&1 | Out-Null
            }
        }
    } catch {}
}
# Watchdog loops (identified by their own PID files, never by cmdline sweeps).
foreach ($pf in @("$LogDir\watchdog.pid")) {
    if (Test-Path $pf) {
        try {
            $wpid = [int](Get-Content $pf -Raw -ErrorAction Stop)
            if ($wpid -gt 0 -and $wpid -ne $PID) { & taskkill /PID $wpid /T /F 2>&1 | Out-Null }
        } catch {}
        Remove-Item $pf -Force -ErrorAction SilentlyContinue
    }
}
if (Test-Path "$LogDir\watchdog_heartbeat.json") {
    try {
        $hbPid = [int](Get-Content "$LogDir\watchdog_heartbeat.json" -Raw | ConvertFrom-Json).pid
        if ($hbPid -gt 0 -and $hbPid -ne $PID) { & taskkill /PID $hbPid /T /F 2>&1 | Out-Null }
    } catch {}
}
Start-Sleep -Seconds 3

# ---- 2. Remove scheduled tasks ---------------------------------------------
Write-Host "Removing scheduled tasks..." -ForegroundColor Yellow
$tasksGone = $true
foreach ($tn in @('Alpha_Autostart', 'Alpha_Watchdog')) {
    $existed = $false
    try { if (Get-ScheduledTask -TaskName $tn -ErrorAction Stop) { $existed = $true } } catch {}
    if ($existed) {
        & schtasks /Delete /TN $tn /F 2>&1 | Out-Null
    }
    $stillThere = $false
    try { if (Get-ScheduledTask -TaskName $tn -ErrorAction Stop) { $stillThere = $true } } catch {}
    if ($stillThere) { $tasksGone = $false }
    Add-Result "Scheduled task $tn removed" (-not $stillThere) $(if ($existed) { "was registered" } else { "was not registered" })
}

# ---- 3. Remove generated launchers / shims / state -------------------------
Write-Host "Removing generated launchers and state..." -ForegroundColor Yellow
$generated = @(
    "$RepoRoot\scripts\autostart\Alpha_Autostart.vbs",
    "$RepoRoot\scripts\autostart\Alpha_Watchdog_Check.vbs",
    "$LogDir\alpha_launch_shim.vbs",
    "$LogDir\alpha_watchdog_shim.vbs",
    "$LogDir\alpha.pid",
    "$LogDir\alpha_health.json",
    "$LogDir\watchdog.pid",
    "$LogDir\watchdog_heartbeat.json",
    "$LogDir\alpha_maintenance.json",
    "$LogDir\recovery_history.jsonl",
    "$LogDir\recovery_history.jsonl.1",
    "$LogDir\verify_recovery_last.json"
)
$stateClean = $true
foreach ($f in $generated) {
    if (Test-Path $f) {
        Remove-Item $f -Force -ErrorAction SilentlyContinue
        if (Test-Path $f) { $stateClean = $false }
    }
}
Add-Result "Generated launchers and state files removed" $stateClean ""

if ($PurgeLogs) {
    Get-ChildItem -Path $LogDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(alpha|watchdog|gateway|frontend|recovery|provider|verify)' } |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
    Write-Host "  Log files purged." -ForegroundColor Gray
}

# ---- 4. Verify nothing autonomous remains ----------------------------------
Write-Host "Verifying that Alpha is fully stopped..." -ForegroundColor Yellow

$loopAlive = $false
$p = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -eq 'powershell.exe' -and $_.CommandLine -like "*$RepoRoot\scripts\watchdog.ps1*"
}
if ($p) { $loopAlive = $true }
Add-Result "No watchdog process remains" (-not $loopAlive) $(if ($p) { "pids: " + ((@($p) | ForEach-Object { $_.ProcessId }) -join ',') } else { "" })

$anyPort = $false
foreach ($port in $ports) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $anyPort = $true }
}
Add-Result "No Alpha service is listening" (-not $anyPort) "ports checked: $($ports -join ',')"

$launcherAlive = $false
$lp = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -eq 'powershell.exe' -and $_.CommandLine -like "*$RepoRoot\start.ps1*"
}
if ($lp) { $launcherAlive = $true }
Add-Result "No launcher process remains" (-not $launcherAlive) $(if ($lp) { "pids: " + ((@($lp) | ForEach-Object { $_.ProcessId }) -join ',') } else { "" })

Add-Result "Maintenance flag written (Alpha stays stopped)" (Test-Path "$LogDir\alpha_maintenance.json") ""
Add-Result "Scheduled tasks removed" $tasksGone ""

# ---- 5. Report --------------------------------------------------------------
Write-Host "`n================= UNINSTALL SUMMARY ==================" -ForegroundColor White
$script:Results | Format-Table -AutoSize
$failed = @($script:Results | Where-Object Result -eq 'FAIL')
if ($failed.Count -eq 0) {
    Write-Host "Alpha autonomous layer uninstalled. Nothing will auto-start or self-heal." -ForegroundColor Green
    Write-Host "To remove Alpha entirely, delete this folder. To restore autonomy, run" -ForegroundColor Gray
    Write-Host "  .\scripts\register_autostart.ps1   (then .\start.ps1)`n" -ForegroundColor Gray
    exit 0
}
Write-Host "UNINSTALL INCOMPLETE: $($failed.Count) check(s) failed - Alpha may still be protected:" -ForegroundColor Red
$failed | ForEach-Object { Write-Host "  - $($_.Check) $($_.Detail)" -ForegroundColor Red }
exit 1
