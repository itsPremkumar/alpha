# Alpha - Stop all running services
# Usage: .\stop.ps1

$ErrorActionPreference = "SilentlyContinue"

Write-Host "`nStopping Alpha services..." -ForegroundColor Yellow

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
