# Agent Workspace - Unified System Launcher for Windows (PowerShell)
# Usage:
#   .\start.ps1               # Start full stack and open web browser
#   .\start.ps1 -NoBrowser    # Start full stack without auto-opening browser
#   .\start.ps1 -Prod         # Start in optimized production mode

[CmdletBinding()]
param (
    [switch]$NoBrowser,
    [switch]$Prod,
    [int]$FrontendPort = 3000,
    [int]$GatewayPort = 8001
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "    Agent Workspace - Unified Super-Agent Platform      " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# -- Helpers ---------------------------------------------------------------
# NOTE: child processes must be killed as a TREE (taskkill /T). `node
# scripts/dev.mjs` spawns `next dev`, which spawns `start-server.js` (the
# actual port holder). Stop-Process kills only one PID, orphaning the
# listener — the next start then dies with EADDRINUSE and the browser
# shows a dead page. That was the recurring "webpage is not working" bug.

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

function Stop-ProcessTree {
    param([int]$ProcessId)
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { "PID $ProcessId" }
    Write-Host "  -> Stopping $name (PID $ProcessId) with child processes..." -ForegroundColor Gray
    try {
        & taskkill /PID $ProcessId /T /F 2>$null | Out-Null
    } catch {}
}

function Free-PortOrExit {
    param([int]$Port)
    $holders = Get-ListeningProcessIds -Port $Port
    foreach ($id in $holders) {
        Stop-ProcessTree -ProcessId $id
    }
    if ($holders.Count -gt 0) {
        # Give the OS a moment to release the socket.
        for ($i = 1; $i -le 15; $i++) {
            Start-Sleep -Seconds 1
            if ((Get-ListeningProcessIds -Port $Port).Count -eq 0) { break }
        }
    }
    $still = Get-ListeningProcessIds -Port $Port
    if ($still.Count -gt 0) {
        Write-Host "`n[ERROR] Port $Port is still in use and could not be freed." -ForegroundColor Red
        foreach ($id in $still) {
            $cmd = "(unknown)"
            try {
                $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue).CommandLine
            } catch {}
            Write-Host "  PID $id : $cmd" -ForegroundColor Red
        }
        Write-Host "Stop that program (or run .\stop.ps1) and try again.`n" -ForegroundColor Yellow
        exit 1
    }
}

function Show-LogTail {
    param([string]$Path)
    if (Test-Path $Path) {
        Write-Host "`n--- tail of $Path ---" -ForegroundColor Gray
        Get-Content $Path -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ -ForegroundColor Gray }
    }
}

function Test-PortListening {
    param([int]$Port)
    return (Get-ListeningProcessIds -Port $Port).Count -gt 0
}

# Windows PowerShell 5.1 copies the environment into a case-insensitive
# dictionary when Start-Process spawns a child. If two variables differ only by
# case -- the very common HTTP_PROXY/http_proxy pair, typically left behind by a
# proxy tool or a package manager -- it raises
# "Item has already been added. Key in dictionary: 'http_proxy'" and the child is
# never created, so neither the Gateway nor the frontend ever starts and the user
# just sees a dead page. Collapse case-duplicates before spawning anything.
function Remove-CaseDuplicateEnvironmentVariables {
    # Go through .NET rather than the `Env:` provider: once two variables differ
    # only by case the provider cannot even enumerate the environment
    # (Get-ChildItem Env: raises "An item with the same key has already been
    # added"), so any provider-based cleanup would fail before it started.
    $names = @()
    try {
        foreach ($key in [System.Environment]::GetEnvironmentVariables().Keys) {
            $names += [string]$key
        }
    } catch {
        return
    }
    $seen = @{}
    foreach ($name in $names) {
        $lower = $name.ToLowerInvariant()
        if ($seen.ContainsKey($lower)) {
            # Passing $null deletes the variable.
            [System.Environment]::SetEnvironmentVariable($name, $null)
        } else {
            $seen[$lower] = $true
        }
    }
}

# A launcher wrapper (notably `uv run`) can hand off / re-exec after the
# service is already answering: the tracked wrapper PID is then gone while
# the real server keeps the port. Only treat an exited wrapper as a crash
# when nothing is listening anymore; otherwise adopt the serving PID so
# monitoring follows the real server instead of crying wolf.
function Update-TrackedProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$Port
    )
    if ($Process -ne $null -and -not $Process.HasExited) {
        return $Process
    }
    $listener = Get-ListeningProcessIds -Port $Port | Select-Object -First 1
    if ($listener -ne $null) {
        $adopted = Get-Process -Id $listener -ErrorAction SilentlyContinue
        if ($adopted -ne $null) {
            return $adopted
        }
    }
    return $Process
}

# -- 1. Locate uv and Node.js ------------------------------------------------
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    $uvCandidates = @(
        "$env:USERPROFILE\.cargo\bin\uv.exe",
        "$env:APPDATA\uv\uv.exe",
        "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    )
    foreach ($c in $uvCandidates) {
        if (Test-Path $c) {
            $dir = Split-Path $c
            $env:PATH = "$dir;" + $env:PATH
            $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
            break
        }
    }
}
if (-not $uvCmd) {
    Write-Host "[!] 'uv' not found. Installing Astral uv package manager..." -ForegroundColor Yellow
    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = "$env:USERPROFILE\.cargo\bin;" + $env:PATH
        $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    } catch {
        Write-Error "Failed to install uv automatically. Please install it from https://astral.sh/uv"
        exit 1
    }
}

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Write-Error "Node.js (v22+) is required. Please install from https://nodejs.org/"
    exit 1
}

# Next.js 16 requires Node.js 22+. An older Node crashes the dev server
# with no obvious message, so check the version up front.
try {
    $nodeMajor = [int]((& node --version).TrimStart("v").Split(".")[0])
} catch {
    $nodeMajor = 0
}
if ($nodeMajor -lt 22) {
    Write-Host "[ERROR] Node.js v22+ is required, found '$(node --version)'. Please upgrade from https://nodejs.org/`n" -ForegroundColor Red
    exit 1
}

# The dev server cannot boot without installed frontend dependencies.
# (Unlike `uv run`, `node` never installs them automatically.)
if (-not (Test-Path "$RepoRoot\frontend\node_modules\next\dist\bin\next")) {
    Write-Host "[ERROR] Frontend dependencies are missing (frontend\node_modules not installed)." -ForegroundColor Red
    Write-Host "Install them first, then re-run this script:" -ForegroundColor Yellow
    Write-Host "  cd frontend" -ForegroundColor Cyan
    Write-Host "  pnpm install   # (or: npm install)`n" -ForegroundColor Cyan
    exit 1
}

# -- 2. Ensure Configurations and Secrets ------------------------------------
# .env
if (-not (Test-Path "$RepoRoot\.env")) {
    Write-Host "Creating .env configuration..." -ForegroundColor Gray
    $secret = [System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")
    if (Test-Path "$RepoRoot\.env.example") {
        Copy-Item "$RepoRoot\.env.example" "$RepoRoot\.env"
    } else {
        New-Item -ItemType File -Path "$RepoRoot\.env" -Force | Out-Null
    }
    Add-Content -Path "$RepoRoot\.env" -Value "`nBETTER_AUTH_SECRET=$secret`nAGENT_WORKSPACE_AUTH_DISABLED=1`n"
}


# config.yaml
if (-not (Test-Path "$RepoRoot\config.yaml")) {
    Write-Host "Creating config.yaml from template..." -ForegroundColor Gray
    Copy-Item "$RepoRoot\config.example.yaml" "$RepoRoot\config.yaml"
}

# extensions_config.json
if (-not (Test-Path "$RepoRoot\extensions_config.json")) {
    Write-Host "Creating extensions_config.json..." -ForegroundColor Gray
    if (Test-Path "$RepoRoot\extensions_config.example.json") {
        Copy-Item "$RepoRoot\extensions_config.example.json" "$RepoRoot\extensions_config.json"
    } else {
        Set-Content -Path "$RepoRoot\extensions_config.json" -Value "{}`n"
    }
}

# Ensure logs directory exists
if (-not (Test-Path "$RepoRoot\logs")) {
    New-Item -ItemType Directory -Path "$RepoRoot\logs" -Force | Out-Null
}

# -- 3. Free the required ports (whole process trees) ------------------------
Write-Host "Checking ports $GatewayPort and $FrontendPort..." -ForegroundColor Gray
Free-PortOrExit -Port $GatewayPort
Free-PortOrExit -Port $FrontendPort
Write-Host "  Ports $GatewayPort and $FrontendPort are free." -ForegroundColor Gray

# -- 4. Set Environment for Single-User Direct Chat --------------------------
$env:AGENT_WORKSPACE_AUTH_DISABLED = "1"
$env:AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL = "http://127.0.0.1:$GatewayPort"
$env:PORT = "$FrontendPort"
$env:PYTHONPATH = "."

# Fresh service logs for this start (see them when something goes wrong).
$gatewayLogOut = "$RepoRoot\logs\gateway.log"
$gatewayLogErr = "$RepoRoot\logs\gateway.err.log"
$frontendLogOut = "$RepoRoot\logs\frontend.log"
$frontendLogErr = "$RepoRoot\logs\frontend.err.log"

# -- 5. Start Backend Gateway ------------------------------------------------
# Collapse case-duplicate environment variables first: Start-Process refuses to
# spawn a child while both HTTP_PROXY and http_proxy exist (see the helper).
Remove-CaseDuplicateEnvironmentVariables

Write-Host "`n[1/2] Starting Gateway API on port $GatewayPort..." -ForegroundColor Yellow
Write-Host "  logs: logs\gateway.log, logs\gateway.err.log" -ForegroundColor Gray

$gatewayProcess = Start-Process -FilePath "uv" `
    -ArgumentList "run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port $GatewayPort" `
    -WorkingDirectory "$RepoRoot\backend" -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $gatewayLogOut -RedirectStandardError $gatewayLogErr

# -- 6. Start Frontend Chat UI -----------------------------------------------
Write-Host "[2/2] Starting Next.js Web Interface on port $FrontendPort..." -ForegroundColor Yellow
Write-Host "  logs: logs\frontend.log, logs\frontend.err.log" -ForegroundColor Gray

$frontendArgs = "node_modules/next/dist/bin/next dev -p $FrontendPort"
if ($Prod) {
    if (-not (Test-Path "$RepoRoot\frontend\.next\BUILD_ID")) {
        Write-Host "Production build not found. Building frontend..." -ForegroundColor Yellow
        Push-Location "$RepoRoot\frontend"
        try {
            & node node_modules/next/dist/bin/next build
            if ($LASTEXITCODE -ne 0) {
                Stop-ProcessTree -ProcessId $gatewayProcess.Id
                throw "Frontend production build failed; startup aborted."
            }
        } finally {
            Pop-Location
        }
    }
    # `next start` takes -p directly (no dev wrapper involved).
    $frontendProcess = Start-Process -FilePath "node" `
        -ArgumentList "node_modules/next/dist/bin/next start -p $FrontendPort" `
        -WorkingDirectory "$RepoRoot\frontend" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr
} else {
    # Pass -p explicitly: relying on $env:PORT alone is fragile, and without
    # it a custom -FrontendPort would boot on 3000 while the browser opens
    # the requested port (blank page).
    $frontendProcess = Start-Process -FilePath "node" `
        -ArgumentList $frontendArgs `
        -WorkingDirectory "$RepoRoot\frontend" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr
}

# -- Helper for Graceful Shutdown --------------------------------------------
function Cleanup-Stack {
    Write-Host "`nShutting down Agent Workspace services gracefully..." -ForegroundColor Yellow
    if ($gatewayProcess -and -not $gatewayProcess.HasExited) {
        Write-Host "  -> Stopping Gateway API tree (PID: $($gatewayProcess.Id))..." -ForegroundColor Gray
        & taskkill /PID $($gatewayProcess.Id) /T /F 2>&1 | Out-Null
    }
    if ($frontendProcess -and -not $frontendProcess.HasExited) {
        Write-Host "  -> Stopping Frontend UI tree (PID: $($frontendProcess.Id))..." -ForegroundColor Gray
        & taskkill /PID $($frontendProcess.Id) /T /F 2>&1 | Out-Null
    }
    # Clean any child uvicorn or next processes on target ports
    foreach ($port in @($GatewayPort, $FrontendPort)) {
        foreach ($id in (Get-ListeningProcessIds -Port $port)) {
            & taskkill /PID $id /T /F 2>&1 | Out-Null
        }
    }
    Write-Host "[OK] All Agent Workspace services stopped cleanly.`n" -ForegroundColor Green
}

# -- 7. Wait for Services to be Ready ----------------------------------------
Write-Host "`nWaiting for services to become healthy..." -ForegroundColor Yellow
Write-Host "(First Next.js compile on Windows can take a few minutes.)" -ForegroundColor Gray

$maxAttempts = 120
$gatewayReady = $false
$frontendReady = $false

for ($i = 1; $i -le $maxAttempts; $i++) {
    Start-Sleep -Seconds 2

    # Check gateway health
    if (-not $gatewayReady) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$GatewayPort/health/ready" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $gatewayReady = $true
                Write-Host "  [OK] Gateway API is healthy on port $GatewayPort." -ForegroundColor Green
            }
        } catch {}
    }

    if (-not $frontendReady) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $frontendReady = $true
                Write-Host "  [OK] Frontend Web UI is serving HTTP 200 on port $FrontendPort." -ForegroundColor Green
            }
        } catch {}
    }

    if ($gatewayReady -and $frontendReady) {
        break
    }

    # A dead wrapper is only a crash when nothing listens anymore (see
    # Update-TrackedProcess): adopt a serving PID across launcher hand-offs.
    $gatewayProcess = Update-TrackedProcess -Process $gatewayProcess -Port $GatewayPort
    $frontendProcess = Update-TrackedProcess -Process $frontendProcess -Port $FrontendPort
    $gatewayGone = ($gatewayProcess -eq $null -or $gatewayProcess.HasExited) -and -not (Test-PortListening -Port $GatewayPort)
    $frontendGone = ($frontendProcess -eq $null -or $frontendProcess.HasExited) -and -not (Test-PortListening -Port $FrontendPort)

    # If a process died early, show WHY (log tails) instead of a mystery.
    if ($gatewayGone) {
        $code = ""
        try { $code = $gatewayProcess.ExitCode } catch {}
        Write-Host "`n[ERROR] Gateway API exited unexpectedly with code $code." -ForegroundColor Red
        Show-LogTail $gatewayLogOut
        Show-LogTail $gatewayLogErr
        Cleanup-Stack
        exit 1
    }
    if ($frontendGone) {
        $code = ""
        try { $code = $frontendProcess.ExitCode } catch {}
        Write-Host "`n[ERROR] Frontend UI exited unexpectedly with code $code." -ForegroundColor Red
        Write-Host "Common cause: another program owned port $FrontendPort. This script frees" -ForegroundColor Yellow
        Write-Host "recorded listeners on start; a process that re-binds the port afterwards" -ForegroundColor Yellow
        Write-Host "will still collide. Run .\stop.ps1, then check the logs below:`n" -ForegroundColor Yellow
        Show-LogTail $frontendLogOut
        Show-LogTail $frontendLogErr
        Cleanup-Stack
        exit 1
    }
}

if (-not $gatewayReady -or -not $frontendReady) {
    Write-Host "`n[ERROR] Startup timed out: gatewayReady=$gatewayReady frontendReady=$frontendReady." -ForegroundColor Red
    Write-Host "The browser will NOT be opened. Inspect the logs:" -ForegroundColor Yellow
    Show-LogTail $gatewayLogOut
    Show-LogTail $gatewayLogErr
    Show-LogTail $frontendLogOut
    Show-LogTail $frontendLogErr
    Cleanup-Stack
    exit 1
}

# -- 8. Open Web Browser -----------------------------------------------------
$appUrl = "http://localhost:$FrontendPort"

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "Agent Workspace is LIVE and running as ONE unified system!" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  Web Application:  " -NoNewline
Write-Host "$appUrl" -ForegroundColor Cyan
Write-Host "  Gateway API:      " -NoNewline
Write-Host "http://127.0.0.1:$GatewayPort" -ForegroundColor Cyan
Write-Host "  Gateway Health:   " -NoNewline
Write-Host "http://127.0.0.1:$GatewayPort/health" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Green
Write-Host "Press [Ctrl+C] to stop all services cleanly.`n" -ForegroundColor Yellow

if (-not $NoBrowser) {
    Write-Host "Opening Agent Workspace in your default web browser..." -ForegroundColor Cyan
    Start-Process $appUrl
}

# -- 9. Keep Running and Monitor ---------------------------------------------
# Monitor the PORTS (effective liveness), not just the launcher wrapper PIDs
# (see Update-TrackedProcess). A service counts as dead only when its port
# goes quiet.
$exitCode = 0
try {
    while ($true) {
        Start-Sleep -Seconds 2
        $gatewayProcess = Update-TrackedProcess -Process $gatewayProcess -Port $GatewayPort
        $frontendProcess = Update-TrackedProcess -Process $frontendProcess -Port $FrontendPort
        $gatewayGone = ($gatewayProcess -eq $null -or $gatewayProcess.HasExited) -and -not (Test-PortListening -Port $GatewayPort)
        $frontendGone = ($frontendProcess -eq $null -or $frontendProcess.HasExited) -and -not (Test-PortListening -Port $FrontendPort)
        if ($gatewayGone -or $frontendGone) {
            if ($gatewayGone) {
                Write-Host "`n[ERROR] Gateway API stopped. See logs\gateway.log / logs\gateway.err.log" -ForegroundColor Red
                Show-LogTail $gatewayLogErr
            }
            if ($frontendGone) {
                Write-Host "`n[ERROR] Frontend UI stopped. See logs\frontend.log / logs\frontend.err.log" -ForegroundColor Red
                Show-LogTail $frontendLogErr
            }
            $exitCode = 1
            break
        }
    }
} finally {
    Cleanup-Stack
}
exit $exitCode
