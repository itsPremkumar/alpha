# Alpha - Stop all running services
# Usage:
#   .\stop.ps1                 # Intentional stop: services + watchdog (maintenance mode)
#   .\stop.ps1 -StopWatchdog   # Same as above (kept for compatibility)
#
# stop.ps1 is the ONLY sanctioned way to stop Alpha. It sets a maintenance
# flag that the watchdog honours, so an intentional stop stays stopped; an
# ordinary crash or taskkill never creates the flag and is always recovered.

[CmdletBinding()]
param(
    [switch]$StopWatchdog   # Legacy no-op: the watchdog is always stood down now
)

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = $PSScriptRoot
$LogDir   = "$RepoRoot\logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ---------------------------------------------------------------------------
# Maintenance flag: written FIRST so the watchdog can never race us and
# resurrect Alpha between our first kill and our last. start.ps1 clears it.
$MaintenanceFile = "$LogDir\alpha_maintenance.json"
try {
    $maint = @{
        active    = $true
        reason    = "intentional stop via stop.ps1"
        pid       = $PID
        since_utc = [DateTime]::UtcNow.ToString("o")
        hostname  = $env:COMPUTERNAME
    } | ConvertTo-Json -Compress
    $tmp = "$MaintenanceFile.$PID.tmp"
    [System.IO.File]::WriteAllText($tmp, $maint)
    Move-Item -Path $tmp -Destination $MaintenanceFile -Force -ErrorAction Stop
    Write-Host "`nMaintenance mode engaged - the watchdog will not restart Alpha." -ForegroundColor Yellow
} catch {
    Write-Host "`nWARNING: could not write maintenance flag: $_" -ForegroundColor Yellow
}

Write-Host "Stopping Alpha services..." -ForegroundColor Yellow

$targetPorts = @(8001, 3000, 8201, 2026)
$killedCount = 0

function Get-ListeningProcessIds {
    param([int]$Port)
    $ids = @()
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        if ($c.OwningProcess -and $c.OwningProcess -ne 0 -and -not ($ids -contains $c.OwningProcess)) {
            $ids += $c.OwningProcess
        }
    }
    return $ids
}

function Stop-Tree {
    param([int]$ProcessId)
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    # /T kills the whole child tree: `node scripts/dev.mjs` -> `next dev` ->
    # `start-server.js`. Killing only the parent orphans the port holder.
    & taskkill /PID $ProcessId /T /F 2>&1 | Out-Null
    return $true
}

# 1. Kill whole trees behind the listening ports
foreach ($port in $targetPorts) {
    foreach ($procId in (Get-ListeningProcessIds -Port $port)) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  -> Terminating tree '$($proc.ProcessName)' (PID: $procId) listening on port $port" -ForegroundColor Gray
            if (Stop-Tree -ProcessId $procId) { $killedCount++ }
        }
    }
}

# 2. Sweep stale Alpha frontend wrappers that hold no port (e.g. a hung
# `next dev` whose listener died but whose compile loop is still running).
try {
    $stale = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*agent-workspace*frontend*" -or $_.CommandLine -like "*alpha*frontend*" }
    foreach ($p in $stale) {
        if (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue) {
            Write-Host "  -> Terminating stale Alpha frontend process (PID: $($p.ProcessId))" -ForegroundColor Gray
            if (Stop-Tree -ProcessId $p.ProcessId) { $killedCount++ }
        }
    }
} catch {}

# 3. Sweep stale Alpha gateway processes.
try {
    $staleGw = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*app.gateway.app*" }
    foreach ($p in $staleGw) {
        if (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue) {
            Write-Host "  -> Terminating stale Alpha gateway process (PID: $($p.ProcessId))" -ForegroundColor Gray
            if (Stop-Tree -ProcessId $p.ProcessId) { $killedCount++ }
        }
    }
} catch {}

if ($killedCount -gt 0) {
    Write-Host "[OK] All Alpha services stopped ($killedCount process tree(s) terminated).`n" -ForegroundColor Green
} else {
    Write-Host "[OK] No active Alpha services were running.`n" -ForegroundColor Green
}

# 4. Always stop the watchdog loop: an intentional stop must stay stopped.
#    The Alpha_Watchdog scheduled task stays enabled, but its -Once pass
#    checks the maintenance flag first and stands down, so nothing comes back.
$wdPidFile   = "$LogDir\watchdog.pid"
$wdHeartbeat = "$LogDir\watchdog_heartbeat.json"
$stoppedWd = @()
foreach ($pf in @($wdPidFile)) {
    if (Test-Path $pf) {
        try {
            $wdPid = [int](Get-Content $pf -Raw -ErrorAction Stop)
            if ($wdPid -gt 0 -and $wdPid -ne $PID -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) {
                & taskkill /PID $wdPid /T /F 2>&1 | Out-Null
                $stoppedWd += $wdPid
            }
        } catch {}
        Remove-Item $pf -Force -ErrorAction SilentlyContinue
    }
}
if (Test-Path $wdHeartbeat) {
    try {
        $w = Get-Content $wdHeartbeat -Raw | ConvertFrom-Json
        if ($w.pid -and [int]$w.pid -ne $PID -and (Get-Process -Id ([int]$w.pid) -ErrorAction SilentlyContinue)) {
            & taskkill /PID ([int]$w.pid) /T /F 2>&1 | Out-Null
            $stoppedWd += [int]$w.pid
        }
    } catch {}
    Remove-Item $wdHeartbeat -Force -ErrorAction SilentlyContinue
}
if ($stoppedWd.Count -gt 0) {
    Write-Host "  -> Watchdog loop stopped (PID $($stoppedWd -join ', '))." -ForegroundColor Gray
}
Write-Host "  Alpha will stay down until you run .\start.ps1 (which clears maintenance)." -ForegroundColor Yellow

# 5. Clean up state files
$pidFile    = "$RepoRoot\logs\alpha.pid"
$healthFile = "$RepoRoot\logs\alpha_health.json"
if (Test-Path $pidFile)    { Remove-Item $pidFile    -Force -ErrorAction SilentlyContinue }
if (Test-Path $healthFile) { Remove-Item $healthFile -Force -ErrorAction SilentlyContinue }
