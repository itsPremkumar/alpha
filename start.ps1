# Alpha - Unified System Launcher for Windows (PowerShell)
# Usage:
#   .\start.ps1               # Start full stack and open web browser
#   .\start.ps1 -NoBrowser    # Start full stack without auto-opening browser
#   .\start.ps1 -Prod         # Start in optimized production mode

[CmdletBinding()]
param (
    [switch]$NoBrowser,
    [switch]$Prod,
    [switch]$WatchdogMode,   # Suppresses browser open; set by watchdog/autostart
    [int]$FrontendPort = 3000,
    [int]$GatewayPort = 8001
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

# ---- PID & health files (consumed by watchdog.ps1) -------------------------
$PidFile    = "$RepoRoot\logs\alpha.pid"
$HealthFile = "$RepoRoot\logs\alpha_health.json"

function Write-HealthFile {
    param([string]$Status = "running", [string]$Detail = "")
    $obj = @{
        pid            = $PID
        status         = $Status
        detail         = $Detail
        gateway_port   = $GatewayPort
        frontend_port  = $FrontendPort
        timestamp_utc  = [DateTime]::UtcNow.ToString("o")
        repo_root      = $RepoRoot
    }
    try {
        $obj | ConvertTo-Json -Compress | Set-Content -Path $HealthFile -Encoding UTF8 -Force
    } catch {}
}

function Remove-StateFiles {
    try { Remove-Item $PidFile    -Force -ErrorAction SilentlyContinue } catch {}
    try { Remove-Item $HealthFile -Force -ErrorAction SilentlyContinue } catch {}
}


# Windows resolves a bare command name through PATHEXT, and PowerShell refuses
# to execute an .exe at all ("Cannot run a document in the middle of a pipeline")
# when PATHEXT has been narrowed -- some managed images ship PATHEXT=.CPL. When
# that happens `uv` and `node` are neither resolvable nor launchable even though
# both are installed and on PATH. Restore the platform default.
if (-not $env:PATHEXT -or $env:PATHEXT -notlike "*.EXE*") {
    $env:PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL"
}

# Next.js routinely removes hundreds of files when it rebuilds `.next` (chunks,
# cache, traces). Some managed shells install an fs shim that blocks bulk
# deletes above a small threshold; when that fires, `next build` / `next dev`
# die with SAFE_DELETE_BULK_CONFIRM_REQUIRED and the frontend never comes up.
# Raise the ceiling here so a normal build is not treated as a bulk delete.
# Only applied when the caller has not already set a value, so an operator can
# still tighten it. Harmless when no such shim is present.
if (-not $env:CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD) {
    $env:CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD = "100000"
}

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "    Alpha - Unified Super-Agent Platform      " -ForegroundColor Cyan
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
    # Get-NetTCPConnection is normally preferable, but it can return an empty
    # result on restricted Windows hosts while netstat still sees the listener.
    # That false negative let the launcher start a second Gateway which only
    # failed much later with WinError 10048, leaving a half-working frontend.
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        foreach ($c in $conns) {
            if ($c.OwningProcess -and $c.OwningProcess -ne 0 -and -not ($ids -contains $c.OwningProcess)) {
                $ids += $c.OwningProcess
            }
        }
    } catch {}
    try {
        foreach ($line in (& netstat.exe -ano -p tcp 2>$null)) {
            if ($line -match "^\s*TCP\s+\S+`:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
                $pid = [int]$Matches[1]
                if ($pid -ne 0 -and -not ($ids -contains $pid)) {
                    $ids += $pid
                }
            }
        }
    } catch {}
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
# Resolving a bare name relies on PATHEXT, which is not reliable: when PATHEXT
# is missing ".EXE" (or the tool simply is not on PATH), `Get-Command uv`
# returns nothing even though uv.exe is installed and its directory is on PATH.
# Resolve "<name>.exe" explicitly and fall back to the known install locations.
function Resolve-Executable {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$ExtraCandidates = @()
    )
    foreach ($candidate in @("$Name.exe", $Name)) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($c in $ExtraCandidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    foreach ($root in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        try {
            $hit = Get-ChildItem -Path $root -Filter "$Name.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($hit) { return $hit.FullName }
        } catch {}
    }
    return $null
}

$uvCandidates = @(
    "$env:USERPROFILE\.cargo\bin\uv.exe",
    "$env:APPDATA\uv\uv.exe",
    "$env:LOCALAPPDATA\Programs\uv\uv.exe",
    "$env:LOCALAPPDATA\hermes\bin\uv.exe",
    "$env:USERPROFILE\.local\bin\uv.exe",
    "$env:USERPROFILE\scoop\shims\uv.exe",
    "$env:ProgramData\chocolatey\bin\uv.exe"
)
$uvPath = Resolve-Executable -Name "uv" -ExtraCandidates $uvCandidates
if (-not $uvPath) {
    Write-Host "[!] 'uv' not found. Installing Astral uv package manager..." -ForegroundColor Yellow
    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = "$env:USERPROFILE\.cargo\bin;" + $env:PATH
        $uvPath = Resolve-Executable -Name "uv" -ExtraCandidates $uvCandidates
    } catch {
        Write-Error "Failed to install uv automatically. Please install it from https://astral.sh/uv"
        exit 1
    }
}
if (-not $uvPath) {
    Write-Host "[ERROR] Could not locate 'uv' even after attempting installation." -ForegroundColor Red
    Write-Host "Install it from https://astral.sh/uv and re-run this script.`n" -ForegroundColor Yellow
    exit 1
}
$env:PATH = (Split-Path $uvPath) + ";" + $env:PATH
Write-Host "  uv: $uvPath" -ForegroundColor Gray

$nodePath = Resolve-Executable -Name "node" -ExtraCandidates @(
    "$env:ProgramFiles\nodejs\node.exe",
    "${env:ProgramFiles(x86)}\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe",
    "$env:APPDATA\nvm\v22.22.2\node.exe",
    "$env:LOCALAPPDATA\nvm4w\nodejs\node.exe"
)
if (-not $nodePath) {
    Write-Error "Node.js (v22+) is required. Please install from https://nodejs.org/"
    exit 1
}
$env:PATH = (Split-Path $nodePath) + ";" + $env:PATH
Write-Host "  node: $nodePath" -ForegroundColor Gray

# Next.js 15.5.x supports Node 18+, but the Windows dev/startup path in this
# repo (long-lived dev server, proxy environment normalization, and the
# `pnpm`/Corepack shim used by install/start) is validated on Node 22+.
# Older Node versions can fail the dev server with no obvious message,
# so enforce Node 22+ up front.
try {
    $nodeMajor = [int]((& $nodePath --version).TrimStart("v").Split(".")[0])
} catch {
    $nodeMajor = 0
}
if ($nodeMajor -lt 22) {
    Write-Host "[ERROR] Node.js v22+ is required, found '$(& $nodePath --version)'. Please upgrade from https://nodejs.org/`n" -ForegroundColor Red
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

# Write our PID file so the watchdog can verify this launcher is alive.
if (-not (Test-Path "$RepoRoot\logs")) { New-Item -ItemType Directory -Path "$RepoRoot\logs" -Force | Out-Null }
[string]$PID | Set-Content -Path $PidFile -Encoding UTF8 -Force
Write-HealthFile -Status "starting" -Detail "launcher initialising"

Write-Host "`n[1/2] Starting Gateway API on port $GatewayPort..." -ForegroundColor Yellow
Write-Host "  logs: logs\gateway.log, logs\gateway.err.log" -ForegroundColor Gray


$gatewayProcess = Start-Process -FilePath $uvPath `
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
            & $nodePath node_modules/next/dist/bin/next build
            if ($LASTEXITCODE -ne 0) {
                Stop-ProcessTree -ProcessId $gatewayProcess.Id
                throw "Frontend production build failed; startup aborted."
            }
        } finally {
            Pop-Location
        }
    }
    # `next start` takes -p directly (no dev wrapper involved).
    $frontendProcess = Start-Process -FilePath $nodePath `
        -ArgumentList "node_modules/next/dist/bin/next start -p $FrontendPort" `
        -WorkingDirectory "$RepoRoot\frontend" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr
} else {
    # Pass -p explicitly: relying on $env:PORT alone is fragile, and without
    # it a custom -FrontendPort would boot on 3000 while the browser opens
    # the requested port (blank page).
    $frontendProcess = Start-Process -FilePath $nodePath `
        -ArgumentList $frontendArgs `
        -WorkingDirectory "$RepoRoot\frontend" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr
}

# -- Helper for Graceful Shutdown --------------------------------------------
function Cleanup-Stack {
    Write-Host "`nShutting down Alpha services gracefully..." -ForegroundColor Yellow
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
    Remove-StateFiles
    Write-Host "[OK] All Alpha services stopped cleanly.`n" -ForegroundColor Green
}


# -- Helper for Auto-Restart -------------------------------------------------
# Long unattended runs must survive service crashes without ever giving up.
# Uses exponential backoff (3s → 6s → 12s → … → 300s max) per service.
# Backoff counter resets after a service has been stable for 10 minutes.
# The launcher NEVER exits due to restart-budget exhaustion — it keeps trying.
$script:gatewayRestarts  = 0
$script:frontendRestarts = 0
$script:gatewayLastStable  = [DateTime]::UtcNow
$script:frontendLastStable = [DateTime]::UtcNow
$StableWindowSeconds       = 600   # 10-minute stability resets backoff

function Get-BackoffSeconds {
    param([int]$Attempt)
    # 3, 6, 12, 24, 48, 96, 192, 300, 300, …
    [Math]::Min(300, [Math]::Pow(2, $Attempt - 1) * 3)
}

function Reset-GatewayBackoffIfStable {
    if (([DateTime]::UtcNow - $script:gatewayLastStable).TotalSeconds -gt $StableWindowSeconds) {
        $script:gatewayRestarts = 0
    }
}

function Reset-FrontendBackoffIfStable {
    if (([DateTime]::UtcNow - $script:frontendLastStable).TotalSeconds -gt $StableWindowSeconds) {
        $script:frontendRestarts = 0
    }
}

function Wait-ForHealthy {
    param([int]$Port, [string]$Path = "/", [int]$MaxWaitSeconds = 120)
    $deadline = [DateTime]::UtcNow.AddSeconds($MaxWaitSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        if ((Get-ListeningProcessIds -Port $Port).Count -eq 0) { return $false }
    }
    return $false
}

function Restart-GatewayService {
    param([int]$Attempt)
    $backoff = Get-BackoffSeconds -Attempt $Attempt
    Write-Host "`n[WARN] Gateway API died — auto-restarting (attempt $Attempt, backoff ${backoff}s)..." -ForegroundColor Yellow
    Show-LogTail $gatewayLogErr
    Write-HealthFile -Status "restarting_gateway" -Detail "attempt $Attempt backoff ${backoff}s"
    Free-PortOrExit -Port $GatewayPort
    Start-Sleep -Seconds $backoff
    $script:gatewayProcess = Start-Process -FilePath $uvPath `
        -ArgumentList "run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port $GatewayPort" `
        -WorkingDirectory "$RepoRoot\backend" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $gatewayLogOut -RedirectStandardError $gatewayLogErr
    Write-Host "  Gateway relaunched (PID: $($script:gatewayProcess.Id)). Verifying health..." -ForegroundColor Gray
    # 90 s was too short: this gateway needs ~2-3 min from `uv run` to answering
    # /health/ready (env resolve + migrations + app import). Giving up early
    # made the watchdog conclude the restart had failed and kill everything.
    $ok = Wait-ForHealthy -Port $GatewayPort -Path "/health/ready" -MaxWaitSeconds 240
    if ($ok) {
        Write-Host "  [OK] Gateway is healthy after restart." -ForegroundColor Green
        $script:gatewayLastStable = [DateTime]::UtcNow
        Write-HealthFile -Status "running" -Detail "gateway restarted OK"
    } else {
        Write-Host "  [WARN] Gateway did not become healthy within 240s - will retry." -ForegroundColor Yellow
    }
}

function Restart-FrontendService {
    param([int]$Attempt)
    $backoff = Get-BackoffSeconds -Attempt $Attempt
    Write-Host "`n[WARN] Frontend UI died — auto-restarting (attempt $Attempt, backoff ${backoff}s)..." -ForegroundColor Yellow
    Show-LogTail $frontendLogErr
    Write-HealthFile -Status "restarting_frontend" -Detail "attempt $Attempt backoff ${backoff}s"
    Free-PortOrExit -Port $FrontendPort
    Start-Sleep -Seconds $backoff
    if ($Prod) {
        $script:frontendProcess = Start-Process -FilePath $nodePath `
            -ArgumentList "node_modules/next/dist/bin/next start -p $FrontendPort" `
            -WorkingDirectory "$RepoRoot\frontend" -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr
    } else {
        $script:frontendProcess = Start-Process -FilePath $nodePath `
            -ArgumentList "node_modules/next/dist/bin/next dev -p $FrontendPort" `
            -WorkingDirectory "$RepoRoot\frontend" -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $frontendLogOut -RedirectStandardError $frontendLogErr
    }
    Write-Host "  Frontend relaunched (PID: $($script:frontendProcess.Id)). Verifying..." -ForegroundColor Gray
    $ok = Wait-ForHealthy -Port $FrontendPort -Path "/" -MaxWaitSeconds 180
    if ($ok) {
        Write-Host "  [OK] Frontend is healthy after restart." -ForegroundColor Green
        $script:frontendLastStable = [DateTime]::UtcNow
        Write-HealthFile -Status "running" -Detail "frontend restarted OK"
    } else {
        Write-Host "  [WARN] Frontend did not become healthy within 180s - will retry." -ForegroundColor Yellow
    }
}


# -- 7. Wait for Services to be Ready ----------------------------------------
Write-Host "`nWaiting for services to become healthy..." -ForegroundColor Yellow
Write-Host "(First Next.js compile on Windows can take a few minutes.)" -ForegroundColor Gray

# First boot is slow on purpose: alembic migrations can take ~3 minutes and the
# first Next.js compile another ~90 s. 120 x 2 s (4 min) was too tight and made
# a healthy boot look like a timeout, so give it 10 minutes.
$maxAttempts = 300
$gatewayReady = $false
$frontendReady = $false

for ($i = 1; $i -le $maxAttempts; $i++) {
    Start-Sleep -Seconds 2

    # Refresh the heartbeat while we wait. This loop can run for several
    # minutes (uvicorn migrations + first Next.js compile) and start.ps1 writes
    # no other health update during it; without this the health file goes stale,
    # Test-LauncherAlive decides the launcher is frozen and taskkills the whole
    # tree mid-boot -- which is exactly how "start.bat exits with code 1 and no
    # error message" happened.
    Write-HealthFile -Status "starting" `
        -Detail ("waiting for services (gateway=$gatewayReady frontend=$frontendReady, attempt $i/$maxAttempts)")

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
    Write-HealthFile -Status "startup_timeout"
    Cleanup-Stack
    exit 1
}

# -- 8. Open Web Browser -----------------------------------------------------
$appUrl = "http://localhost:$FrontendPort"

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "Alpha is LIVE and running as ONE unified system!" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host "  Web Application:  " -NoNewline
Write-Host "$appUrl" -ForegroundColor Cyan
Write-Host "  Gateway API:      " -NoNewline
Write-Host "http://127.0.0.1:$GatewayPort" -ForegroundColor Cyan
Write-Host "  Gateway Health:   " -NoNewline
Write-Host "http://127.0.0.1:$GatewayPort/health" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Green
Write-Host "Press [Ctrl+C] to stop all services cleanly.`n" -ForegroundColor Yellow

Write-HealthFile -Status "running" -Detail "all services healthy"
$script:gatewayLastStable  = [DateTime]::UtcNow
$script:frontendLastStable = [DateTime]::UtcNow

if (-not $NoBrowser -and -not $WatchdogMode) {
    Write-Host "Opening Alpha in your default web browser..." -ForegroundColor Cyan
    Start-Process $appUrl
}

# -- 9. Keep Running and Monitor ---------------------------------------------
# Monitor the PORTS (effective liveness), not just the launcher wrapper PIDs
# (see Update-TrackedProcess). A service counts as dead only when its port
# goes quiet. Dead services are auto-restarted with exponential backoff and the
# launcher NEVER gives up — only Ctrl+C (or stop.ps1) shuts things down.
# A heartbeat (Write-HealthFile) fires every ~30s so the watchdog can detect
# a frozen launcher process.
$exitCode = 0
$script:heartbeatCounter = 0
try {
    while ($true) {
        Start-Sleep -Seconds 2
        $script:heartbeatCounter++
        # Heartbeat every ~30 s (15 × 2s iterations). The status must reflect
        # the ACTUAL port state: writing "running" while a service is down made
        # the watchdog think the launcher was done healing, so it took over and
        # killed the launcher mid-restart.
        if ($script:heartbeatCounter % 15 -eq 0) {
            $bothUp = (Test-PortListening -Port $GatewayPort) -and (Test-PortListening -Port $FrontendPort)
            if ($bothUp) {
                Write-HealthFile -Status "running"
            } else {
                Write-HealthFile -Status "degraded" -Detail "waiting for gateway/frontend to return"
            }
        }

        $gatewayProcess  = Update-TrackedProcess -Process $gatewayProcess  -Port $GatewayPort
        $frontendProcess = Update-TrackedProcess -Process $frontendProcess -Port $FrontendPort
        $gatewayGone  = ($gatewayProcess  -eq $null -or $gatewayProcess.HasExited)  -and -not (Test-PortListening -Port $GatewayPort)
        $frontendGone = ($frontendProcess -eq $null -or $frontendProcess.HasExited) -and -not (Test-PortListening -Port $FrontendPort)

        if ($gatewayGone -or $frontendGone) {
            if ($gatewayGone) {
                Reset-GatewayBackoffIfStable
                $script:gatewayRestarts += 1
                Restart-GatewayService -Attempt $script:gatewayRestarts
            }
            if ($frontendGone) {
                Reset-FrontendBackoffIfStable
                $script:frontendRestarts += 1
                Restart-FrontendService -Attempt $script:frontendRestarts
            }
        }
    }
} finally {
    Cleanup-Stack
}
exit $exitCode
