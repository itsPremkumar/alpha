# Alpha Watchdog — Independent process monitor
# Runs as a separate background process. Detects when Alpha services (Gateway + Frontend)
# are down and restarts them by relaunching start.ps1 -NoBrowser -WatchdogMode.
# Also detects a frozen/missing launcher (stale health file) and kills+relaunches it.
#
# Usage:
#   .\scripts\watchdog.ps1                  # Start watchdog loop (blocks)
#   .\scripts\watchdog.ps1 -Once           # Single check-and-heal pass (for scheduled task trigger)
#   .\scripts\watchdog.ps1 -StartIfDown    # Start if either service is missing (at-logon trigger)
#   .\scripts\watchdog.ps1 -Stop           # Stop the running watchdog

[CmdletBinding()]
param (
    [switch]$Once,
    [switch]$StartIfDown,
    [switch]$Stop
)

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot   = Split-Path $PSScriptRoot -Parent
$LogDir     = "$RepoRoot\logs"
$WatchdogLog  = "$LogDir\watchdog.log"
$WatchdogPid  = "$LogDir\watchdog.pid"
$AlphaPid     = "$LogDir\alpha.pid"
$HealthFile   = "$LogDir\alpha_health.json"
$StartScript  = "$RepoRoot\start.ps1"
$GatewayPort  = 8001
$FrontendPort = 3000
$HeartbeatMaxAgeSeconds = 120   # health file older than this = frozen launcher
# start.ps1 legitimately needs several minutes before anything listens on
# 8001/3000 (alembic migrations ~3 min, first Next.js compile ~90 s). Until it
# finishes, ports are down by design, so recovery must wait instead of
# taskkilling the launcher it is supposed to be babysitting.
$StartupGraceSeconds = 600
# How many consecutive 30 s checks the watchdog will let a recovering launcher
# work on its own before taking over (8 x 30 s = 4 min, enough for the
# gateway's own 240 s health wait plus backoff).
$MaxDeferredRecoveries = 8

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-WatchdogLog {
    param([string]$Message, [string]$Level = "INFO")
    $ts  = [DateTime]::Now.ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$ts][$Level] $Message"
    try { Add-Content -Path $WatchdogLog -Value $line -Encoding UTF8 -Force } catch {}
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    elseif ($Level -eq "WARN")  { Write-Host $line -ForegroundColor Yellow }
    else  { Write-Host $line }
}

# ---- Stop: kill the running watchdog ----------------------------------------
if ($Stop) {
    if (Test-Path $WatchdogPid) {
        $wdPid = [int](Get-Content $WatchdogPid -Raw -ErrorAction SilentlyContinue)
        if ($wdPid -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) {
            Write-Host "Stopping Alpha Watchdog (PID $wdPid)..."
            & taskkill /PID $wdPid /T /F 2>$null | Out-Null
        }
        Remove-Item $WatchdogPid -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[OK] Watchdog stopped."
    exit 0
}

# ---- Helpers ----------------------------------------------------------------
function Get-ListeningPids {
    param([int]$Port)
    $ids = @()
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
                if ($pid -ne 0 -and -not ($ids -contains $pid)) { $ids += $pid }
            }
        }
    } catch {}
    return $ids
}

function Test-ServiceAlive {
    param([int]$Port)
    return (Get-ListeningPids -Port $Port).Count -gt 0
}

function Test-GatewayHealthy {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$GatewayPort/health/ready" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Get-HealthState {
    # Returns @{ Status; AgeSeconds } from alpha_health.json, or $null.
    if (-not (Test-Path $HealthFile)) { return $null }
    try {
        $obj = Get-Content $HealthFile -Raw | ConvertFrom-Json
        $mtime = (Get-Item $HealthFile -ErrorAction SilentlyContinue).LastWriteTimeUtc
        $age = ([DateTime]::UtcNow - $mtime).TotalSeconds
        return @{ Status = [string]$obj.status; AgeSeconds = $age }
    } catch { return $null }
}

function Get-RecoveryDeferral {
    # Returns a reason string when the watchdog should WAIT instead of
    # intervening, or $null when it may act.
    #
    # start.ps1 is itself self-healing (it relaunches a dead gateway/frontend
    # and verifies health). Racing it -- taskkilling the launcher while it is
    # 100 s into a 2-3 min gateway restart -- turned a recoverable single-service
    # crash into a full-stack restart, which is what made startup look broken.
    if (-not (Test-Path $AlphaPid)) { return $null }
    $procId = [int](Get-Content $AlphaPid -Raw -ErrorAction SilentlyContinue)
    if (-not $procId) { return $null }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }

    $health = Get-HealthState
    $status = if ($health) { [string]$health.Status } else { "" }

    # Initial boot: ports are down by design until migrations + Next build done.
    if ($status -eq "starting") { return "launcher is starting (status=starting)" }
    if (-not $status) {
        try {
            $uptime = ([DateTime]::UtcNow - $proc.StartTime.ToUniversalTime()).TotalSeconds
            if ($uptime -lt $StartupGraceSeconds) {
                return "launcher is $([int]$uptime)s old and has not reported health yet"
            }
        } catch {}
    }

    # Launcher is actively healing a service itself - let it finish.
    if ($status -like "restarting_*" -or $status -eq "degraded") {
        return "launcher is recovering (status=$status)"
    }
    return $null
}

function Test-LauncherAlive {
    # Check if start.ps1 is running and has updated its health file recently
    if (-not (Test-Path $AlphaPid)) { return $false }
    $pidVal = [int](Get-Content $AlphaPid -Raw -ErrorAction SilentlyContinue)
    if (-not $pidVal) { return $false }
    $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    # Also check heartbeat freshness. A launcher that is still starting gets a
    # longer leash: its first boot legitimately takes minutes.
    $health = Get-HealthState
    if ($health) {
        $limit = if ($health.Status -eq "running") { $HeartbeatMaxAgeSeconds } else { $StartupGraceSeconds }
        if ($health.AgeSeconds -gt $limit) {
            Write-WatchdogLog "Launcher PID $pidVal is alive but health file is $([int]$health.AgeSeconds)s old (status=$($health.Status), frozen?)" "WARN"
            # Kill the frozen launcher so we can restart it
            try { & taskkill /PID $pidVal /T /F 2>$null | Out-Null } catch {}
            return $false
        }
    }
    return $true
}

function Get-AlphaRestartBackoffSeconds {
    param([int]$Attempt)
    [Math]::Min(300, [Math]::Pow(2, [Math]::Max(0, $Attempt - 1)) * 5)
}

function Start-AlphaStack {
    param([int]$Attempt = 1)
    $backoff = Get-AlphaRestartBackoffSeconds -Attempt $Attempt
    Write-WatchdogLog "Launching Alpha stack (attempt $Attempt, backoff ${backoff}s)..." "WARN"
    if ($backoff -gt 0) { Start-Sleep -Seconds $backoff }

    # Resolve uv and node paths the same way start.ps1 does
    $uvCandidates = @(
        "$env:USERPROFILE\.cargo\bin\uv.exe",
        "$env:APPDATA\uv\uv.exe",
        "$env:LOCALAPPDATA\Programs\uv\uv.exe",
        "$env:LOCALAPPDATA\hermes\bin\uv.exe",
        "$env:USERPROFILE\.local\bin\uv.exe"
    )
    $uvPath = $null
    foreach ($c in $uvCandidates) { if (Test-Path $c) { $uvPath = $c; break } }
    $uvCmd = Get-Command uv.exe -ErrorAction SilentlyContinue
    if (-not $uvPath -and $uvCmd) { $uvPath = $uvCmd.Source }

    $nodePath = $null
    foreach ($c in @("C:\nvm4w\nodejs\node.exe","$env:ProgramFiles\nodejs\node.exe","$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
        if (Test-Path $c) { $nodePath = $c; break }
    }
    $nodeCmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $nodePath -and $nodeCmd) { $nodePath = $nodeCmd.Source }

    # Start-Process inherits this watchdog's environment, so set the child's
    # requirements here. (A hashtable was previously built for this and never
    # applied, which left task-scheduler launches with a bare PATH.)
    if ($uvPath) { $env:PATH = (Split-Path $uvPath) + ";" + $env:PATH }
    if ($nodePath) { $env:PATH = (Split-Path $nodePath) + ";" + $env:PATH }
    $env:AGENT_WORKSPACE_AUTH_DISABLED = "1"
    if (-not $env:PATHEXT -or $env:PATHEXT -notlike "*.EXE*") {
        $env:PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL"
    }

    # Launch start.ps1 hidden via a new PowerShell process
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -NoBrowser -WatchdogMode"
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden
    Write-WatchdogLog "Alpha launcher started: PID $($proc.Id)"
    return $proc
}

# ---- Single-check mode (for scheduled task -Once/-StartIfDown) --------------
if ($Once -or $StartIfDown) {
    $gwAlive = Test-ServiceAlive -Port $GatewayPort
    $feAlive = Test-ServiceAlive -Port $FrontendPort
    $launchAlive = Test-LauncherAlive

    Write-WatchdogLog "Health check: gateway=$gwAlive frontend=$feAlive launcher=$launchAlive"

    if ($gwAlive -and $feAlive -and $launchAlive) {
        Write-WatchdogLog "All services healthy - nothing to do."
        exit 0
    }

    # The launcher may be booting or healing a service itself; in that case
    # starting a second watchdog/recovery would only fight it.
    $deferral = Get-RecoveryDeferral
    if ($deferral) {
        Write-WatchdogLog "Deferring recovery: $deferral - nothing to do."
        exit 0
    }

    # Check whether the watchdog loop itself is already running
    if (Test-Path $WatchdogPid) {
        $wdPid = [int](Get-Content $WatchdogPid -Raw -ErrorAction SilentlyContinue)
        if ($wdPid -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) {
            Write-WatchdogLog "Watchdog loop is already running (PID $wdPid) - leaving it to handle recovery."
            exit 0
        }
    }

    # Start the persistent watchdog loop in the background
    Write-WatchdogLog "Starting persistent watchdog loop in background..." "WARN"
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`""
    $wdProc = Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs -PassThru -WindowStyle Hidden
    Write-WatchdogLog "Watchdog loop started: PID $($wdProc.Id)"
    exit 0
}

# ---- Persistent watchdog loop -----------------------------------------------
Write-WatchdogLog "Alpha Watchdog started (PID $PID). Monitoring every 30s."
[string]$PID | Set-Content -Path $WatchdogPid -Encoding UTF8 -Force

$script:alphaRestarts = 0
$script:deferCount = 0
$script:lastLaunchTime = [DateTime]::MinValue

# Rotate log if >5 MB
try {
    if ((Get-Item $WatchdogLog -ErrorAction SilentlyContinue).Length -gt 5MB) {
        $backup = $WatchdogLog -replace "\.log$", ".old.log"
        Move-Item -Path $WatchdogLog -Destination $backup -Force -ErrorAction SilentlyContinue
    }
} catch {}

try {
    while ($true) {
        Start-Sleep -Seconds 30

        $gwAlive     = Test-ServiceAlive -Port $GatewayPort
        $feAlive     = Test-ServiceAlive -Port $FrontendPort
        $launchAlive = Test-LauncherAlive

        if ($gwAlive -and $feAlive -and $launchAlive) {
            # All good - reset backoff after 10 minutes of consecutive health
            if (([DateTime]::UtcNow - $script:lastLaunchTime).TotalMinutes -gt 10) {
                $script:alphaRestarts = 0
            }
            $script:deferCount = 0
            continue
        }

        # Something is wrong
        $status = "gateway=$gwAlive frontend=$feAlive launcher=$launchAlive"

        # ...unless start.ps1 is itself starting up or healing the service:
        # during that window the ports being down is expected, and killing the
        # launcher here would convert a one-service crash into a full restart.
        $deferral = Get-RecoveryDeferral
        if ($deferral) {
            if ($script:deferCount -lt $MaxDeferredRecoveries) {
                $script:deferCount++
                Write-WatchdogLog "Deferring recovery ($script:deferCount/$MaxDeferredRecoveries): $deferral [$status]"
                continue
            }
            Write-WatchdogLog "Launcher still unhealthy after $script:deferCount deferred checks - taking over. [$status]" "WARN"
        }
        $script:deferCount = 0

        Write-WatchdogLog "Unhealthy: $status - initiating recovery." "WARN"

        # Kill stale launcher (if any) before restarting
        if (Test-Path $AlphaPid) {
            $stale = [int](Get-Content $AlphaPid -Raw -ErrorAction SilentlyContinue)
            if ($stale -and (Get-Process -Id $stale -ErrorAction SilentlyContinue)) {
                Write-WatchdogLog "Killing stale launcher PID $stale"
                try { & taskkill /PID $stale /T /F 2>$null | Out-Null } catch {}
                Start-Sleep -Seconds 3
            }
        }

        $script:alphaRestarts += 1
        $script:lastLaunchTime = [DateTime]::UtcNow
        Start-AlphaStack -Attempt $script:alphaRestarts

        # Wait for services to come up (up to 3 minutes)
        Write-WatchdogLog "Waiting up to 180s for Alpha services to come up..."
        $deadline = [DateTime]::UtcNow.AddSeconds(180)
        while ([DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Seconds 5
            if ((Test-ServiceAlive -Port $GatewayPort) -and (Test-ServiceAlive -Port $FrontendPort)) {
                Write-WatchdogLog "Alpha services are up after restart." "INFO"
                break
            }
        }
        if (-not (Test-ServiceAlive -Port $GatewayPort) -or -not (Test-ServiceAlive -Port $FrontendPort)) {
            Write-WatchdogLog "Services still not up after 180s - will retry on next cycle." "WARN"
        }
    }
} finally {
    Remove-Item $WatchdogPid -Force -ErrorAction SilentlyContinue
    Write-WatchdogLog "Watchdog exiting."
}
