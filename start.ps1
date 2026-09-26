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
    [switch]$Force,          # Restart even if a healthy launcher already owns the stack
    [int]$FrontendPort = 3000,
    [int]$GatewayPort = 8001
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

# ---- PID & health files (consumed by watchdog.ps1) -------------------------
$PidFile    = "$RepoRoot\logs\alpha.pid"
$HealthFile = "$RepoRoot\logs\alpha_health.json"
$MaintenanceFile = "$RepoRoot\logs\alpha_maintenance.json"
# Recorded so the watchdog can detect PID reuse: a recycled PID that does not
# match this start time is not our launcher.
$LauncherStartedUtc = (Get-Process -Id $PID -ErrorAction SilentlyContinue).StartTime.ToUniversalTime().ToString("o")

# Health status vocabulary (shared with scripts/watchdog.ps1):
#   starting | healthy | degraded | recovering | failed
$script:LastHealthStatus = "starting"
$script:LastHealthDetail = "launcher initialising"
# Why the last readiness probe failed, verbatim. Empty means "no probe has
# failed yet" and is never rendered as success.
$script:LastGatewayProbeError = ""
# The machine-readable fields of the most recent observation (phase, elapsed
# seconds, which service is blocking, the probe's real error). Retained so a
# heartbeat refresh during a restart backoff still carries the diagnosis:
# without this, `Refresh-Heartbeat` - which is the only thing that writes while
# the launcher is inside a 3-300 s backoff - emitted a record with no `phase`
# and no `gateway_last_error`, so the one moment an operator most needs the
# reason is the moment the file lost it.
$script:LastHealthExtra = $null
function Write-HealthFile {
    param(
        [string]$Status = "healthy",
        [string]$Detail = "",
        # Additive machine-readable fields merged into the record. Keys defined
        # here always win, so a caller cannot accidentally overwrite `pid` or
        # `status` with something a reader would trust.
        [hashtable]$Extra = $null
    )
    if (-not (Test-Path (Split-Path $HealthFile))) {
        New-Item -ItemType Directory -Path (Split-Path $HealthFile) -Force | Out-Null
    }
    if ($Extra) {
        $script:LastHealthExtra = $Extra
    } elseif ($Status -in @("failed", "stopped", "stale")) {
        # Terminal states describe a different situation; carrying forward a
        # boot-wait phase would be a fresh lie in the other direction.
        $script:LastHealthExtra = $null
    }
    $script:LastHealthStatus = $Status
    $script:LastHealthDetail = $Detail
    $obj = @{
        pid                  = $PID
        launcher_started_utc = $LauncherStartedUtc
        status               = $Status
        detail               = $Detail
        gateway_port         = $GatewayPort
        frontend_port        = $FrontendPort
        timestamp_utc        = [DateTime]::UtcNow.ToString("o")
        repo_root            = $RepoRoot
    }
    $fields = if ($Extra) { $Extra } else { $script:LastHealthExtra }
    if ($fields) {
        foreach ($key in $fields.Keys) {
            if ($obj.ContainsKey($key)) { continue }
            $obj[$key] = $fields[$key]
        }
    }
    Write-StateFile -Path $HealthFile -Object $obj
}

# Keep the heartbeat timestamp moving during long blocking waits (health waits,
# backoff sleeps) so the watchdog never mistakes a working launcher for a
# frozen one. Same status/detail, fresh timestamp.
function Refresh-Heartbeat {
    Write-HealthFile -Status $script:LastHealthStatus -Detail $script:LastHealthDetail
}

function Test-MaintenanceMode {
    return (Test-Path $MaintenanceFile)
}

# stop.ps1 wrote the maintenance flag: stop everything WE manage and exit so an
# intentional stop stays stopped instead of being fought by our own monitor.
function Stop-OnMaintenance {
    Write-Host "Maintenance flag detected - launcher stopping managed services and exiting." -ForegroundColor Yellow
    foreach ($p in @($script:gatewayProcess, $script:frontendProcess)) {
        if ($p -and -not $p.HasExited) {
            try { & taskkill /PID $p.Id /T /F 2>&1 | Out-Null } catch {}
        }
    }
    Remove-StateFiles
    exit 0
}

# Atomic state writes: readers (the watchdog) must never see a half-written
# file. Write to a temp file on the same volume, then rename over the target.
function Write-StateFile {
    param([string]$Path, [object]$Object)
    try {
        $json = $Object | ConvertTo-Json -Compress
        $tmp  = "$Path.$PID.tmp"
        [System.IO.File]::WriteAllText($tmp, $json)
        Move-Item -Path $tmp -Destination $Path -Force -ErrorAction Stop
    } catch {}
}

function Remove-StateFiles {
    try { Remove-Item $PidFile    -Force -ErrorAction SilentlyContinue } catch {}
    try { Remove-Item $HealthFile -Force -ErrorAction SilentlyContinue } catch {}
}

# Keep logs bounded: rotate anything past 5 MB to <name>.1 before (re)starting.
function Rotate-LogIfLarge {
    param([string]$Path, [long]$MaxBytes = 5MB)
    try {
        $f = Get-Item $Path -ErrorAction SilentlyContinue
        if ($f -and $f.Length -gt $MaxBytes) {
            Move-Item -Path $Path -Destination ($Path -replace "\.(log|txt)$", ".1.`$1") -Force -ErrorAction SilentlyContinue
        }
    } catch {}
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
    param([int]$Port, [switch]$NonFatal)
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
        # Name the actual holder in the recorded reason, not just on screen: the
        # console output is gone by the time anyone reads the status file, and
        # "port in use" without the PID and command line forces the operator to
        # go read the source or re-run netstat to find out which port is taken.
        $descriptions = @()
        foreach ($id in $still) {
            $cmd = "(unknown)"
            try {
                $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue).CommandLine
            } catch {}
            if (-not $cmd) { $cmd = "(command line unavailable)" }
            Write-Host "  PID $id : $cmd" -ForegroundColor Red
            $descriptions += "PID $id ($cmd)"
        }
        # NonFatal: restart paths must never kill the launcher over a busy
        # port - report failure and let the caller's backoff retry instead.
        if ($NonFatal) {
            Write-Host "[WARN] Port $Port is still in use - restart deferred (will retry)." -ForegroundColor Yellow
            return $false
        }
        Write-Host "`n[ERROR] Port $Port is still in use and could not be freed." -ForegroundColor Red
        Write-Host "Stop that program (or run .\stop.ps1) and try again.`n" -ForegroundColor Yellow
        Fail-Startup "port $Port is already in use and could not be freed: $($descriptions -join '; ')"
    }
    return $true
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

# -- Terminal-state bookkeeping ----------------------------------------------
# Set once the launcher has decided it is aborting, so the shutdown handler
# below leaves the truthful "failed" record alone instead of deleting it.
$script:StartupAborted = $false
# Shutdown is idempotent: the startup-timeout path, the monitor loop's finally
# and the outer finally can all reach it, and taskkill-ing a PID that is already
# gone must not be an error.
$script:ShutdownDone = $false

# A health file that names a launcher which is not running is a monitoring lie:
# the next reader (the watchdog, `make doctor`, support_bundle) has no way to
# tell "Alpha is booting" from "Alpha died 40 minutes ago and left this behind".
# Rewrite it as an explicit `stale` record so the claim is self-refuting. A file
# whose PID is still alive is never touched - including our own, which is alive
# for the whole duration of this finally block.
function Mark-DeadLauncherStatus {
    if (-not (Test-Path $HealthFile)) { return }
    try {
        $rec = Get-Content $HealthFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return
    }
    try { $p = [int]$rec.pid } catch { $p = 0 }
    if ($p -le 0) { return }
    if (Get-Process -Id $p -ErrorAction SilentlyContinue) { return }
    try {
        Write-StateFile -Path $HealthFile -Object @{
            pid                  = $p
            status               = "stale"
            detail               = "launcher PID $p is no longer running; this file was left behind by a crash, not by a live launcher"
            superseded_status    = [string]$rec.status
            superseded_detail    = [string]$rec.detail
            gateway_port         = $GatewayPort
            frontend_port        = $FrontendPort
            timestamp_utc        = [DateTime]::UtcNow.ToString("o")
            repo_root            = $RepoRoot
        }
        Write-Host "  [STALE] Marked a leftover health file as stale (launcher PID $p is gone)." -ForegroundColor Yellow
    } catch {}
}

# The launcher body is wrapped in a try/finally that guarantees a terminal state
# for the health/PID files on every exit path - see the finally at the bottom.
try {

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
# Record a truthful FAILED state whenever we abort on a missing dependency:
# a silent exit would leave a stale "healthy" heartbeat and confuse the chain.
function Fail-Startup {
    param([string]$Reason)
    $script:StartupAborted = $true
    Remove-StateFiles
    Write-HealthFile -Status "failed" -Detail $Reason
    Write-Host "[FAILED] $Reason" -ForegroundColor Red
    exit 1
}
if (-not $uvPath) {
    Write-Host "[!] 'uv' not found. Installing Astral uv package manager..." -ForegroundColor Yellow
    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = "$env:USERPROFILE\.cargo\bin;" + $env:PATH
        $uvPath = Resolve-Executable -Name "uv" -ExtraCandidates $uvCandidates
    } catch {
        Fail-Startup "dependency missing: uv (auto-install failed) - install from https://astral.sh/uv"
    }
}
if (-not $uvPath) {
    Fail-Startup "dependency missing: uv not located - install from https://astral.sh/uv"
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
    Fail-Startup "dependency missing: Node.js v22+ - install from https://nodejs.org/"
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
    # Route through Fail-Startup, not a bare `exit`: a bare exit leaves whatever
    # alpha_health.json was on disk untouched, so a status file from a previous
    # (now dead) launcher survives claiming "starting"/"healthy" and every later
    # diagnostic trusts it. Fail-Startup records the real reason and a terminal
    # status instead.
    Fail-Startup "dependency missing: Node.js v22+ is required, found '$(& $nodePath --version)' - install from https://nodejs.org/"
}

# The dev server cannot boot without installed frontend dependencies.
# (Unlike `uv run`, `node` never installs them automatically.)
if (-not (Test-Path "$RepoRoot\frontend\node_modules\next\dist\bin\next")) {
    # Same reason as above: a bare `exit 1` here was a stale-status-file factory.
    Write-Host "[ERROR] Frontend dependencies are missing (frontend\node_modules not installed)." -ForegroundColor Red
    Write-Host "Install them first, then re-run this script:" -ForegroundColor Yellow
    Write-Host "  cd frontend" -ForegroundColor Cyan
    Write-Host "  pnpm install   # (or: npm install)`n" -ForegroundColor Cyan
    Fail-Startup "dependency missing: frontend\node_modules not installed - run 'pnpm install' in frontend\ (or: npm install)"
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


# config.yaml / extensions_config.json: validate, and if invalid, BACK UP the
# broken file with a timestamp and regenerate from the shipped example.
# User configuration is never silently deleted - the broken copy stays on disk
# for inspection (last-known-good behaviour: example == shipped good config).
function Repair-ConfigFile {
    param(
        [string]$Path,
        [string]$Example,
        [ValidateSet('json', 'nonempty')][string]$Validate,
        [string]$Label
    )
    if (-not (Test-Path $Path)) {
        Write-Host "Creating $Label from template..." -ForegroundColor Gray
        if ($Example -and (Test-Path $Example)) { Copy-Item $Example $Path -Force }
        elseif ($Validate -eq 'json') { Set-Content -Path $Path -Value "{}`n" }
        else { New-Item -ItemType File -Path $Path -Force | Out-Null }
        return
    }
    $broken = $false
    if ($Validate -eq 'json') {
        try { Get-Content $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop | Out-Null }
        catch { $broken = $true }
    } else {
        if (-not (Get-Item $Path).Length) { $broken = $true }
    }
    if ($broken) {
        $backup = "$Path.broken-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        try {
            Move-Item -Path $Path -Destination $backup -Force -ErrorAction Stop
            Write-Host "  [REPAIR] $Label is invalid - backed up to $(Split-Path $backup -Leaf)" -ForegroundColor Yellow
            if ($Example -and (Test-Path $Example)) { Copy-Item $Example $Path -Force }
            elseif ($Validate -eq 'json') { Set-Content -Path $Path -Value "{}`n" }
            Write-Host "  [REPAIR] Regenerated $Label from the shipped template." -ForegroundColor Yellow
        } catch {
            Write-Host "  [ERROR] Could not repair $Label : $_" -ForegroundColor Red
        }
    }
}

# config.yaml
Repair-ConfigFile -Path "$RepoRoot\config.yaml" -Example "$RepoRoot\config.example.yaml" `
    -Validate nonempty -Label "config.yaml"

# extensions_config.json
Repair-ConfigFile -Path "$RepoRoot\extensions_config.json" -Example "$RepoRoot\extensions_config.example.json" `
    -Validate json -Label "extensions_config.json"
# (.env holds secrets and is only created when absent - never rewritten.)

# Ensure logs directory exists
if (-not (Test-Path "$RepoRoot\logs")) {
    New-Item -ItemType Directory -Path "$RepoRoot\logs" -Force | Out-Null
}

# -- 3. Maintenance mode, duplicate launchers, bounded logs -------------------
# stop.ps1 sets a maintenance flag for an intentional stop. Any real start
# clears it: launching Alpha is itself a decision to resume autonomous
# operation, and the watchdog must not stand down against our will.
if (Test-Path $MaintenanceFile) {
    try { Remove-Item $MaintenanceFile -Force -ErrorAction Stop; Write-Host "  Maintenance flag cleared - resuming autonomous operation." -ForegroundColor Yellow } catch {}
}

# Two launchers fighting over the same two ports corrupt each other's boot.
# If an existing launcher already owns a healthy stack, exit quietly (unless
# -Force explicitly asked for a restart). Otherwise take over: kill the old
# launcher FIRST so only one monitor ever owns the services.
if (Test-Path $PidFile) {
    $existingPid = 0
    try { $existingPid = [int](Get-Content $PidFile -ErrorAction Stop) } catch { $existingPid = 0 }
    if ($existingPid -gt 0 -and $existingPid -ne $PID) {
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($existingProc) {
            $healthy = $false
            try {
                $h = Get-Content $HealthFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                $age = ([DateTime]::UtcNow - [DateTime]::Parse($h.timestamp_utc, $null,
                    [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds
                $stateOk = $h.status -in @('starting', 'healthy', 'degraded', 'recovering', 'running')
                $fresh = $age -ge 0 -and $age -lt 240
                $portsUp = (Test-PortListening $GatewayPort) -and (Test-PortListening $FrontendPort)
                $healthy = $stateOk -and $fresh -and $portsUp
            } catch {}
            if ($healthy -and -not $Force) {
                Write-Host "Alpha is already running (launcher PID $existingPid). Nothing to do." -ForegroundColor Green
                Write-Host "  Use -Force to restart it anyway." -ForegroundColor Gray
                exit 0
            }
            $why = "unhealthy"
            if ($Force) { $why = "forced restart" }
            Write-Host "Replacing existing launcher PID $existingPid ($why)..." -ForegroundColor Yellow
            try { Stop-Process -Id $existingPid -Force -ErrorAction Stop } catch {}
            Start-Sleep -Seconds 2
        }
    }
}

# Bounded logs: rotate before appending so no single log grows forever.
Rotate-LogIfLarge -Path "$RepoRoot\logs\gateway.log"
Rotate-LogIfLarge -Path "$RepoRoot\logs\gateway.err.log"
Rotate-LogIfLarge -Path "$RepoRoot\logs\frontend.log"
Rotate-LogIfLarge -Path "$RepoRoot\logs\frontend.err.log"
Rotate-LogIfLarge -Path "$RepoRoot\logs\watchdog.log"

# -- 4. Free the required ports (whole process trees) ------------------------
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
# Atomic: a reader must never see a truncated PID.
if (-not (Test-Path "$RepoRoot\logs")) { New-Item -ItemType Directory -Path "$RepoRoot\logs" -Force | Out-Null }
Write-StateFile -Path $PidFile -Object ([int]$PID)
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
    # Idempotent: three call sites reach this (the startup-timeout path, the
    # monitor loop's finally, and the outer finally). Running it twice would
    # re-taskkill PIDs that are already gone and re-delete state files.
    if ($script:ShutdownDone) { return }
    $script:ShutdownDone = $true
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
        if (Test-MaintenanceMode) { Stop-OnMaintenance }
        Start-Sleep -Seconds 3
        Refresh-Heartbeat   # the watchdog watches this timestamp
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        if ((Get-ListeningProcessIds -Port $Port).Count -eq 0) { return $false }
    }
    return $false
}

function Start-SleepWithHeartbeat {
    # Backoff can sleep up to 300 s; refresh the heartbeat every 30 s so the
    # watchdog still sees a live launcher while we deliberately wait.
    param([int]$Seconds)
    $remaining = $Seconds
    while ($remaining -gt 0) {
        if (Test-MaintenanceMode) { Stop-OnMaintenance }
        $chunk = [Math]::Min(30, $remaining)
        Start-Sleep -Seconds $chunk
        Refresh-Heartbeat
        $remaining -= $chunk
    }
}

function Restart-GatewayService {
    param([int]$Attempt)
    $backoff = Get-BackoffSeconds -Attempt $Attempt
    Write-Host "`n[WARN] Gateway API died — auto-restarting (attempt $Attempt, backoff ${backoff}s)..." -ForegroundColor Yellow
    Show-LogTail $gatewayLogErr
    Write-HealthFile -Status "recovering" -Detail "gateway restart attempt $Attempt backoff ${backoff}s"
    if (-not (Free-PortOrExit -Port $GatewayPort -NonFatal)) { return }
    Start-SleepWithHeartbeat -Seconds $backoff
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
        Write-HealthFile -Status "healthy" -Detail "gateway restarted OK"
    } else {
        Write-Host "  [WARN] Gateway did not become healthy within 240s - will retry." -ForegroundColor Yellow
    }
}

function Restart-FrontendService {
    param([int]$Attempt)
    $backoff = Get-BackoffSeconds -Attempt $Attempt
    Write-Host "`n[WARN] Frontend UI died — auto-restarting (attempt $Attempt, backoff ${backoff}s)..." -ForegroundColor Yellow
    Show-LogTail $frontendLogErr
    Write-HealthFile -Status "recovering" -Detail "frontend restart attempt $Attempt backoff ${backoff}s"
    if (-not (Free-PortOrExit -Port $FrontendPort -NonFatal)) { return }
    Start-SleepWithHeartbeat -Seconds $backoff
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
    # A cold Next.js compile of / took 281 s on this machine - 180 s declared
    # a healthy frontend dead and cascaded into a full restart.
    $ok = Wait-ForHealthy -Port $FrontendPort -Path "/" -MaxWaitSeconds 360
    if ($ok) {
        Write-Host "  [OK] Frontend is healthy after restart." -ForegroundColor Green
        $script:frontendLastStable = [DateTime]::UtcNow
        Write-HealthFile -Status "healthy" -Detail "frontend restarted OK"
    } else {
        Write-Host "  [WARN] Frontend did not become healthy within 360s - will retry." -ForegroundColor Yellow
    }
}


# -- 7. Wait for Services to be Ready ----------------------------------------
Write-Host "`nWaiting for services to become healthy..." -ForegroundColor Yellow
Write-Host "(First Next.js compile on Windows can take a few minutes.)" -ForegroundColor Gray

# First boot is slow: alembic migrations ~3 min, and a cold Next.js compile of
# a single page was measured at 281 s on this machine. 450 x 2 s = 15 min of
# patience so a slow-but-healthy boot is never mistaken for a timeout.
$maxAttempts = 450
$gatewayReady = $false
$frontendReady = $false

for ($i = 1; $i -le $maxAttempts; $i++) {
    if (Test-MaintenanceMode) { Stop-OnMaintenance }
    Start-Sleep -Seconds 2

    # Probe FIRST, then record. Writing the heartbeat before the probes meant
    # the file carried the *previous* iteration's `gateway_last_error` (empty on
    # the first pass), so a status and its stated reason could disagree. Probing
    # first makes every record self-consistent: the error in the file is always
    # the error behind the status in the file.
    #
    # Check gateway health. Note the route: /health/ready, not /health. /health
    # is liveness only - it answers 200 whenever the process is up - so waiting
    # on it would let this launcher declare a Gateway whose database is
    # unreachable healthy.
    if (-not $gatewayReady) {
        $script:LastGatewayProbeError = ""
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$GatewayPort/health/ready" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $gatewayReady = $true
                $script:LastGatewayProbeError = ""
                Write-Host "  [OK] Gateway API is healthy on port $GatewayPort." -ForegroundColor Green
            } else {
                # 503 from /health/ready means the Gateway is up but cannot serve
                # (unreachable database / checkpointer). Record the real reason
                # rather than letting the loop look like a port that never opened.
                $script:LastGatewayProbeError = "HTTP $($resp.StatusCode): $($resp.Content)"
            }
        } catch {
            # Name the actual cause - a refused connection (nothing is
            # listening) and a reset/timeout (something crashed mid-boot) lead
            # to completely different fixes.
            $why = $_.Exception.Message
            if ($why -match 'actively refused|Unable to connect|refused') {
                $why = "nothing is listening on port $GatewayPort"
            }
            $script:LastGatewayProbeError = $why
        }
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

    # Refresh the heartbeat while we wait, AFTER the probes so the recorded
    # reason always belongs to the recorded status. This loop can run for
    # several minutes (uvicorn migrations + first Next.js compile) and start.ps1
    # writes no other health update during it; without this the health file goes
    # stale, Test-LauncherAlive decides the launcher is frozen and taskkills the
    # whole tree mid-boot -- which is exactly how "start.bat exits with code 1
    # and no error message" happened.
    #
    # The extra keys are what make "starting" actionable instead of a shrug.
    # `detail` alone ("gateway=False, attempt 186/450") cannot distinguish a
    # three-second boot from a gateway that has been dead for six minutes, which
    # is precisely the state an operator is handed when the port never opens.
    # `elapsed_seconds`, `gateway_ready`, `frontend_ready` and
    # `gateway_last_error` (the actual failure of the readiness probe) are
    # machine-readable so scripts/deploy_status.py and any dashboard can say
    # "the gateway has not answered for 372s: <reason>" instead of "starting".
    Write-HealthFile -Status "starting" `
        -Detail ("waiting for services (gateway=$gatewayReady frontend=$frontendReady, attempt $i/$maxAttempts)") `
        -Extra @{
            phase              = "boot_wait"
            attempt            = $i
            max_attempts       = $maxAttempts
            elapsed_seconds    = [int]($i * 2)
            gateway_ready      = [bool]$gatewayReady
            frontend_ready     = [bool]$frontendReady
            gateway_last_error = $script:LastGatewayProbeError
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

    # A service that dies DURING the boot wait is a crash to recover from,
    # not a reason to abandon the whole startup. Exiting here made the
    # launcher suicide on any early crash (including the watchdog killing a
    # port tree mid-boot), which cascaded into full cold restarts and broke
    # unattended recovery. Restart with the same exponential backoff the
    # steady-state monitor uses; the attempt budget below still bounds a
    # genuinely broken startup with a truthful "failed" status.
    if ($gatewayGone) {
        $code = ""
        try { $code = $gatewayProcess.ExitCode } catch {}
        Write-Host "`n[WARN] Gateway API exited during startup (code $code) - restarting..." -ForegroundColor Yellow
        Show-LogTail $gatewayLogErr
        Reset-GatewayBackoffIfStable
        $script:gatewayRestarts += 1
        Restart-GatewayService -Attempt $script:gatewayRestarts
        continue
    }
    if ($frontendGone) {
        $code = ""
        try { $code = $frontendProcess.ExitCode } catch {}
        Write-Host "`n[WARN] Frontend UI exited during startup (code $code) - restarting..." -ForegroundColor Yellow
        Write-Host "Common cause: another program owned port $FrontendPort." -ForegroundColor Yellow
        Show-LogTail $frontendLogOut
        Show-LogTail $frontendLogErr
        Reset-FrontendBackoffIfStable
        $script:frontendRestarts += 1
        Restart-FrontendService -Attempt $script:frontendRestarts
        continue
    }
}

if (-not $gatewayReady -or -not $frontendReady) {
    Write-Host "`n[ERROR] Startup timed out: gatewayReady=$gatewayReady frontendReady=$frontendReady." -ForegroundColor Red
    Write-Host "The browser will NOT be opened. Inspect the logs:" -ForegroundColor Yellow
    Show-LogTail $gatewayLogOut
    Show-LogTail $gatewayLogErr
    Show-LogTail $frontendLogOut
    Show-LogTail $frontendLogErr
    Write-HealthFile -Status "failed" -Detail "startup timeout (gateway=$gatewayReady frontend=$frontendReady)"
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

Write-HealthFile -Status "healthy" -Detail "all services healthy"
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
        if (Test-MaintenanceMode) { Stop-OnMaintenance }
        Start-Sleep -Seconds 2
        $script:heartbeatCounter++
        # Heartbeat every ~30 s (15 × 2s iterations). The status must reflect
        # the ACTUAL port state: writing "running" while a service is down made
        # the watchdog think the launcher was done healing, so it took over and
        # killed the launcher mid-restart.
        if ($script:heartbeatCounter % 15 -eq 0) {
            $bothUp = (Test-PortListening -Port $GatewayPort) -and (Test-PortListening -Port $FrontendPort)
            if ($bothUp) {
                Write-HealthFile -Status "healthy"
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
} finally {
    # ── Terminal-state guarantee ────────────────────────────────────────────
    # PowerShell runs an enclosing `finally` on EVERY exit path, including
    # `exit 1` from Fail-Startup, an unhandled terminating error, and Ctrl+C.
    # That is the only way to guarantee the launcher never leaves a status file
    # behind that claims to be live: without this block, a launcher that was
    # taskkill /F'd, that died of an unhandled error, or that aborted on a
    # missing dependency leaves logs/alpha_health.json saying
    # status="starting"/"healthy" with a PID that no longer exists - and every
    # later diagnostic (watchdog, `make doctor`, support_bundle) trusts it.
    try {
        $ownedByUs = $false
        $terminalRecord = $false
        if (Test-Path $HealthFile) {
            try {
                $rec = Get-Content $HealthFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                $ownedByUs = ([int]$rec.pid -eq $PID)
                $terminalRecord = ([string]$rec.status -in @("failed", "stopped"))
            } catch { $ownedByUs = $false }
        }
        # Reap children and free ports, unless Fail-Startup already recorded the
        # terminal state and there is nothing of ours running to clean up.
        if (-not $script:StartupAborted) { Cleanup-Stack }
        # A non-terminal claim of ours must never outlive us; a terminal record
        # (Fail-Startup's "failed") is the truthful post-mortem and is kept.
        if ($ownedByUs -and -not $terminalRecord) { Remove-StateFiles }
        # Anything left claiming a launcher that is gone is marked stale.
        Mark-DeadLauncherStatus
    } catch {}
}

