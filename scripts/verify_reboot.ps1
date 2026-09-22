# Alpha - Post-reboot autonomous recovery check (Layer 4 -> 3 -> 2 -> 1)
#
# Run this AFTER logging in again following a reboot. It does not start
# anything: it only OBSERVES whether Windows brought Alpha back on its own
# through the scheduled tasks, and reports PASS/FAIL per layer.
#
#   powershell -ExecutionPolicy Bypass -File scripts\verify_reboot.ps1
#   ... -Wait 600     # keep waiting up to 600 s for the stack to come up
#
# Expected chain:  logon -> Alpha_Autostart task -> watchdog loop (-Once)
#                 -> launcher (start.ps1) -> gateway + frontend -> healthy.

[CmdletBinding()]
param(
    [int]$Wait = 0    # extra seconds to wait for the stack to become healthy
)

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = Split-Path $PSScriptRoot -Parent
$LogDir   = "$RepoRoot\logs"

$script:Results = New-Object System.Collections.ArrayList
function Record([string]$Name, [bool]$Pass, [string]$Detail = "") {
    $status = if ($Pass) { "PASS" } else { "FAIL" }
    $color  = if ($Pass) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1}{2}" -f $status, $Name, $(if ($Detail) { " - $Detail" } else { "" })) -ForegroundColor $color
    [void]$script:Results.Add([pscustomobject]@{ Check = $Name; Result = $status; Detail = $Detail })
}
function Test-GwUp {
    try { return ((Invoke-WebRequest 'http://127.0.0.1:8001/health/ready' -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop).StatusCode -eq 200) }
    catch { return $false }
}
function Test-FeUp {
    try { return ((Invoke-WebRequest 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop).StatusCode -eq 200) }
    catch { return $false }
}
function Get-PidSafe([string]$Path) {
    try { return [int](Get-Content $Path -Raw -ErrorAction Stop) } catch { return 0 }
}

Write-Host "`n=== Alpha post-reboot recovery check ===" -ForegroundColor Cyan
try { $up = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; Write-Host "Last boot: $up`n" -ForegroundColor Gray } catch {}

# Layer 4: scheduled tasks must exist and have run since boot.
foreach ($tn in @('Alpha_Autostart', 'Alpha_Watchdog')) {
    $t = $null; $info = $null
    try { $t = Get-ScheduledTask -TaskName $tn -ErrorAction Stop; $info = Get-ScheduledTaskInfo -TaskName $tn -ErrorAction Stop } catch {}
    $ranSinceBoot = $false
    if ($info -and $info.LastRunTime -and $up -and $info.LastRunTime -gt $up) { $ranSinceBoot = $true }
    Record "L4: task $tn ran after boot" ($t -and $ranSinceBoot) `
        "state=$(if ($t) { $t.State } else { 'missing' }) lastRun=$($info.LastRunTime) lastResult=$($info.LastTaskResult)"
}

# Layer 3: watchdog loop alive with a fresh heartbeat.
$wdPid = Get-PidSafe "$LogDir\watchdog.pid"
$wdAlive = ($wdPid -gt 0 -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue))
$hbAge = -1; $hbFresh = $false
try {
    $hb = Get-Content "$LogDir\watchdog_heartbeat.json" -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $hbAge = [int](([DateTime]::UtcNow - [DateTime]::Parse($hb.timestamp_utc, $null,
        [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds)
    $hbFresh = ($hbAge -ge 0 -and $hbAge -le 90) -and ([int]$hb.pid -eq $wdPid)
} catch {}
Record "L3: watchdog loop alive" $wdAlive "pid=$wdPid"
Record "L3: watchdog heartbeat fresh (<=90s)" $hbFresh "age=${hbAge}s"

# Layer 2: launcher alive with a fresh heartbeat.
$lpPid = Get-PidSafe "$LogDir\alpha.pid"
$lpAlive = ($lpPid -gt 0 -and (Get-Process -Id $lpPid -ErrorAction SilentlyContinue))
$hStatus = ""; $hAge = -1; $hFresh = $false
try {
    $h = Get-Content "$LogDir\alpha_health.json" -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $hStatus = [string]$h.status
    $hAge = [int](([DateTime]::UtcNow - [DateTime]::Parse($h.timestamp_utc, $null,
        [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds)
    $hFresh = ($hAge -ge 0 -and $hAge -le 360)
} catch {}
Record "L2: launcher alive" $lpAlive "pid=$lpPid"
Record "L2: launcher heartbeat fresh (<=360s)" $hFresh "status=$hStatus age=${hAge}s"

# No maintenance flag left over from before the reboot.
$maint = Test-Path "$LogDir\alpha_maintenance.json"
Record "Maintenance flag not set" (-not $maint) ""

# Layer 1: wait (optionally longer with -Wait) for both services.
$deadline = (Get-Date).AddSeconds([Math]::Max($Wait, 30))
$gw = Test-GwUp; $fe = Test-FeUp
while ((-not ($gw -and $fe)) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 10
    $gw = Test-GwUp; $fe = Test-FeUp
}
Record "L1: gateway healthy (/health/ready)" $gw ""
Record "L1: frontend healthy (HTTP 200)" $fe ""
Record "Health status is 'healthy'" ($hStatus -eq 'healthy') "status=$hStatus"

Write-Host "`n================= REBOOT CHECK SUMMARY ================" -ForegroundColor White
$script:Results | Format-Table -AutoSize
$fail = @($script:Results | Where-Object Result -eq 'FAIL').Count
if ($fail -eq 0) {
    Write-Host "REBOOT RECOVERY VERIFIED: Alpha returned to full operation unattended." -ForegroundColor Green
    exit 0
}
Write-Host "REBOOT RECOVERY INCOMPLETE: $fail check(s) failed." -ForegroundColor Red
Write-Host "Inspect: logs\watchdog.log, Get-ScheduledTaskInfo Alpha_Watchdog (LastTaskResult)" -ForegroundColor Yellow
exit 1
