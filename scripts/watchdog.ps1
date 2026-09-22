# Alpha Watchdog - Layer 3 independent supervisor (with Layer 4 watchdog-of-watchdog)
#
# Architecture (no layer is responsible for its own recovery):
#   Layer 4  Windows Task Scheduler  -> Alpha_Watchdog task runs -Once every 5 min
#            and Alpha_Autostart at logon. -Once verifies THIS loop is alive,
#            running the right installation, with a fresh heartbeat, and
#            (re)creates it when it is missing, frozen, or stale.
#   Layer 3  This script's loop      -> monitors gateway/frontend/launcher,
#            escalates: defer -> component restart -> full stack restart.
#   Layer 2  start.ps1               -> monitors and restarts its children.
#   Layer 1  gateway, frontend, workers.
#
# Detachment: launcher and watchdog instances are spawned through tiny VBScript
# shims that exit immediately, so they are ORPHANS of whoever created them.
# Killing this watchdog (Layer 3) therefore cannot kill the launcher (Layer 2),
# and killing the scheduled task shell cannot kill this watchdog.
#
# Maintenance: stop.ps1 writes logs\alpha_maintenance.json. While it exists,
# every layer stands down - that is the only sanctioned way to keep Alpha
# stopped. Crashes and taskkill never create the flag and are always recovered.
#
# Usage:
#   .\scripts\watchdog.ps1             # Start watchdog loop (blocks)
#   .\scripts\watchdog.ps1 -Once       # Layer 4 pass: verify/recreate the loop
#   .\scripts\watchdog.ps1 -StartIfDown# Alias of -Once (logon trigger)
#   .\scripts\watchdog.ps1 -Stop       # Stop the running loop

[CmdletBinding()]
param (
    [switch]$Once,
    [switch]$StartIfDown,
    [switch]$Stop
)

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot         = Split-Path $PSScriptRoot -Parent
$LogDir           = "$RepoRoot\logs"
$WatchdogLog      = "$LogDir\watchdog.log"
$WatchdogPid      = "$LogDir\watchdog.pid"
$WatchdogHeartbeat= "$LogDir\watchdog_heartbeat.json"
$AlphaPid         = "$LogDir\alpha.pid"
$HealthFile       = "$LogDir\alpha_health.json"
$MaintenanceFile  = "$LogDir\alpha_maintenance.json"
$RecoveryHistory  = "$LogDir\recovery_history.jsonl"
$StartScript      = "$RepoRoot\start.ps1"
$WatchdogScript   = "$RepoRoot\scripts\watchdog.ps1"
$GatewayPort      = 8001
$FrontendPort     = 3000

$CheckIntervalSeconds     = 30
$WatchdogHeartbeatMaxAge  = 90    # loop heartbeat older than this = frozen loop
$LauncherHeartbeatMaxAge  = 360   # start.ps1 can legitimately pause up to 300 s in backoff
$StartupGraceSeconds      = 600   # a launcher younger than this is still in first boot
$MaxDeferredRecoveries    = 8     # checks we let a live launcher heal itself first
$MaxComponentRecoveries   = 3     # per-component restarts before escalating to the stack
$GatewayHungThreshold     = 6     # port up but no HTTP answer x 30 s = 3 min
$FrontendHungThreshold    = 12    # cold Next.js compile measured 281 s - allow 6 min
$ActionBackoffStart       = 30    # full-stack restart backoff (doubles, capped)
$ActionBackoffCap         = 300

$script:WatchdogStartedUtc = [DateTime]::UtcNow.ToString("o")
$script:GwHttpFail   = 0
$script:FeHttpFail   = 0
$script:ConsecutiveFailures = 0
$script:ComponentAttempts   = @{}
$script:LastActionUtc = $null
$script:ActionBackoff = 0
$script:MaintenanceLogged = $false

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ---------------------------------------------------------------- logging ----
function Write-WdLog {
    param([string]$Message, [string]$Level = "INFO")
    try {
        $log = Get-Item $WatchdogLog -ErrorAction SilentlyContinue
        if ($log -and $log.Length -gt 5MB) {
            Move-Item -Path $WatchdogLog -Destination "$WatchdogLog.1" -Force -ErrorAction SilentlyContinue
        }
        Add-Content -Path $WatchdogLog -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message" -Encoding UTF8
    } catch {}
}

# Persistent, rotation-bounded recovery history (JSON lines, no secrets).
function Write-RecoveryEvent {
    param([string]$Component, [string]$Action, [string]$Result, [string]$Reason = "")
    try {
        $evt = @{
            timestamp_utc = [DateTime]::UtcNow.ToString("o")
            pid           = $PID
            component     = $Component
            action        = $Action
            result        = $Result
            reason        = $Reason
        } | ConvertTo-Json -Compress
        $f = Get-Item $RecoveryHistory -ErrorAction SilentlyContinue
        if ($f -and $f.Length -gt 5MB) {
            Move-Item -Path $RecoveryHistory -Destination "$RecoveryHistory.1" -Force -ErrorAction SilentlyContinue
        }
        Add-Content -Path $RecoveryHistory -Value $evt -Encoding UTF8
    } catch {}
    Write-WdLog "$Component :: $Action -> $Result$(if ($Reason) { " ($Reason)" })"
}

# Atomic state writes: readers must never observe a half-written JSON file.
function Write-StateFile {
    param([string]$Path, [object]$Object)
    try {
        $json = $Object | ConvertTo-Json -Compress
        $tmp  = "$Path.$PID.tmp"
        [System.IO.File]::WriteAllText($tmp, $json)
        Move-Item -Path $tmp -Destination $Path -Force -ErrorAction Stop
    } catch {}
}

function Write-Heartbeat {
    param([string]$Status, [string]$Stack = "")
    Write-StateFile -Path $WatchdogHeartbeat -Object @{
        pid                    = $PID
        component              = "watchdog"
        version                = "2"
        started_utc            = $script:WatchdogStartedUtc
        timestamp_utc          = [DateTime]::UtcNow.ToString("o")
        status                 = $Status
        repo_root              = $RepoRoot
        check_interval_seconds = $CheckIntervalSeconds
        stack                  = $Stack
    }
}

# ------------------------------------------------------------- maintenance ---
function Test-Maintenance {
    if (-not (Test-Path $MaintenanceFile)) { return $false }
    try {
        $m = Get-Content $MaintenanceFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        return [bool]$m.active
    } catch {
        # Unreadable flag: respect the operator's stop rather than resurrect Alpha.
        return $true
    }
}

# --------------------------------------------------------------- primitives --
function Test-PortListening {
    param([int]$Port)
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1)
    } catch { return $false }
}

function Test-HttpOk {
    param([int]$Port, [string]$Path, [int]$TimeoutSec = 6)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing `
            -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}

function Get-HealthState {
    if (-not (Test-Path $HealthFile)) { return $null }
    try { return (Get-Content $HealthFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop) }
    catch { return $null }   # corrupted/partially-written JSON: treat as absent
}

function Get-PidFileValue {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return 0 }
    try { return [int](Get-Content $Path -Raw -ErrorAction Stop) } catch { return 0 }
}

function Get-FileAgeSeconds {
    param([string]$TimestampUtc)
    if (-not $TimestampUtc) { return [double]::MaxValue }
    try {
        $t = [DateTime]::Parse($TimestampUtc, $null,
            [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
        $age = ([DateTime]::UtcNow - $t).TotalSeconds
        if ($age -lt 0) { return 0 }
        return $age
    } catch { return [double]::MaxValue }
}

# Layer 2 liveness with PID-reuse detection: a recycled PID whose process start
# time does not match what the launcher recorded is NOT our launcher.
function Test-LauncherAlive {
    $lp = Get-PidFileValue -Path $AlphaPid
    if ($lp -le 0 -or $lp -eq $PID) { return $false }
    $proc = Get-Process -Id $lp -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    $h = Get-HealthState
    if ($h -and $h.pid -eq $lp -and $h.launcher_started_utc) {
        try {
            $recorded = [DateTime]::Parse($h.launcher_started_utc, $null,
                [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
            if ([math]::Abs(($proc.StartTime.ToUniversalTime() - $recorded).TotalSeconds) -gt 30) {
                Write-WdLog "Launcher PID $lp is a recycled PID (start-time mismatch) - treating as stale" "WARN"
                return $false
            }
        } catch {}
    }
    return $true
}

function Test-LauncherHeartbeatFresh {
    param($Health, [double]$MaxAge = $LauncherHeartbeatMaxAge)
    if (-not $Health) { return $false }
    $age = Get-FileAgeSeconds $Health.timestamp_utc
    return ($age -le $MaxAge)
}

function Test-WatchdogHeartbeatFresh {
    param([int]$ExpectedPid, [double]$MaxAge = $WatchdogHeartbeatMaxAge)
    if (-not (Test-Path $WatchdogHeartbeat)) { return $false }
    try {
        $hb = Get-Content $WatchdogHeartbeat -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ([int]$hb.pid -ne $ExpectedPid) { return $false }
        return ((Get-FileAgeSeconds $hb.timestamp_utc) -le $MaxAge)
    } catch { return $false }
}

# ------------------------------------------------------ detached launching ----
# The shim is a 5-line VBScript that launches the target hidden and exits
# immediately, so the target becomes an orphan of the shim. Nothing that
# created the shim can later kill the target by killing its own process tree.
function Write-Shim {
    param([string]$Name, [string]$CommandLine)
    $vbs = "$LogDir\$Name"
    $dir = $RepoRoot -replace '"', '""'
    $cmd = $CommandLine -replace '"', '""'   # VBScript string escaping
    $text = "Option Explicit`r`n" +
            "Dim sh`r`n" +
            "Set sh = CreateObject(`"WScript.Shell`")`r`n" +
            "sh.CurrentDirectory = `"$dir`"`r`n" +
            "sh.Run `"$cmd`", 0, False`r`n"
    try {
        # BOM-free ANSI: wscript rejects a UTF-8 BOM on line 1.
        $tmp = "$vbs.$PID.tmp"
        [System.IO.File]::WriteAllText($tmp, $text, [System.Text.Encoding]::Default)
        Move-Item -Path $tmp -Destination $vbs -Force -ErrorAction Stop
    } catch { Write-WdLog "Failed to write shim $vbs : $($_.Exception.Message)" "ERROR"; return $null }
    return $vbs
}

function Invoke-Detached {
    param([string]$ShimPath)
    if (-not $ShimPath) { return $false }
    try {
        Start-Process -FilePath "wscript.exe" -ArgumentList @('//B', '//Nologo', "`"$ShimPath`"") `
            -WindowStyle Hidden -ErrorAction Stop | Out-Null
        return $true
    } catch { return $false }
}

function Stop-StaleLauncher {
    $lp = Get-PidFileValue -Path $AlphaPid
    if ($lp -gt 0 -and $lp -ne $PID -and (Get-Process -Id $lp -ErrorAction SilentlyContinue)) {
        & taskkill /PID $lp /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 1
    }
}

# Kill only what holds a port: the smallest recovery that unblocks the
# launcher's own component monitor (it sees the child exit and restarts it).
function Stop-PortTree {
    param([int]$Port)
    # Returns "killed" (we freed a bound port), "already-free" (the port was
    # vacant before we acted - usually the launcher mid-restart) or "stuck".
    # The distinction keeps recovery logs honest: a vacuous pass must not be
    # reported as a kill.
    $result = "stuck"
    for ($round = 1; $round -le 3; $round++) {
        $ids = @()
        try {
            $ids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique)
        } catch {}
        if (-not $ids) { $result = $(if ($round -eq 1) { "already-free" } else { "killed" }); break }
        foreach ($id in $ids) {
            if ($id -gt 0 -and $id -ne $PID) { & taskkill /PID $id /T /F 2>&1 | Out-Null }
        }
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Seconds 2
            if (-not (Test-PortListening -Port $Port)) { $result = "killed"; break }
        }
        if ($result -eq "killed") { break }
    }
    return $result
}

function Restart-Component {
    param([string]$Component)
    $port = if ($Component -eq "gateway") { $GatewayPort } else { $FrontendPort }
    $n = 0
    if ($script:ComponentAttempts.ContainsKey($Component)) { $n = $script:ComponentAttempts[$Component] }
    Write-RecoveryEvent -Component $Component -Action "component_restart" `
        -Result "attempt" -Reason "consecutive failures=$($script:ConsecutiveFailures), attempt $n - killing port $port tree; the launcher restarts it"
    $outcome = Stop-PortTree -Port $port
    if ($outcome -eq "killed") {
        Write-RecoveryEvent -Component $Component -Action "component_restart" -Result "killed" -Reason "port $port freed"
    } elseif ($outcome -eq "already-free") {
        Write-RecoveryEvent -Component $Component -Action "component_restart" -Result "noop" -Reason "port $port already free before kill (launcher likely mid-restart)"
    } else {
        Write-RecoveryEvent -Component $Component -Action "component_restart" -Result "failed" -Reason "port $port still bound"
    }
    return ($outcome -ne "stuck")
}

# Full-stack recovery: replace the launcher (if any) and start a fresh one,
# detached, through the VBS shim.
function Start-AlphaStack {
    param([string]$Reason)
    Stop-StaleLauncher
    $shimCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -NoBrowser -WatchdogMode"
    $shim = Write-Shim -Name "alpha_launch_shim.vbs" -CommandLine $shimCmd
    if (Invoke-Detached -ShimPath $shim) {
        Write-RecoveryEvent -Component "stack" -Action "full_restart" -Result "spawned" -Reason $Reason
        return $true
    }
    Write-RecoveryEvent -Component "stack" -Action "full_restart" -Result "spawn_failed" -Reason $Reason
    return $false
}

# ------------------------------------------------------------- layer-3 pass ---
function Get-StackSnapshot {
    $gwPort = Test-PortListening -Port $GatewayPort
    $fePort = Test-PortListening -Port $FrontendPort

    if ($gwPort) {
        if (Test-HttpOk -Port $GatewayPort -Path "/health/ready") {
            $script:GwHttpFail = 0; $gw = "up"
        } else {
            $script:GwHttpFail++
            $gw = if ($script:GwHttpFail -ge $GatewayHungThreshold) { "hung" } else { "starting" }
        }
    } else { $script:GwHttpFail = 0; $gw = "down" }

    if ($fePort) {
        if (Test-HttpOk -Port $FrontendPort -Path "/") {
            $script:FeHttpFail = 0; $fe = "up"
        } else {
            $script:FeHttpFail++
            $fe = if ($script:FeHttpFail -ge $FrontendHungThreshold) { "hung" } else { "starting" }
        }
    } else { $script:FeHttpFail = 0; $fe = "down" }

    $health      = Get-HealthState
    $launcherOk  = Test-LauncherAlive
    $heartFresh  = Test-LauncherHeartbeatFresh -Health $health
    $status      = if ($health -and $health.status) { [string]$health.status } else { "unknown" }

    $summary = "gateway=$gw frontend=$fe launcher=$(if ($launcherOk) { 'alive' } else { 'dead' }) " +
               "launcher_hb=$(if ($heartFresh) { 'fresh' } else { 'stale' }) status=$status"
    return @{
        GatewayState = $gw; FrontendState = $fe
        LauncherAlive = $launcherOk; HeartbeatFresh = $heartFresh
        Status = $status; Health = $health; Summary = $summary
    }
}

function Invoke-HealthCheck {
    $s = Get-StackSnapshot

    $everythingOk = ($s.GatewayState -eq "up") -and ($s.FrontendState -eq "up") `
        -and $s.LauncherAlive -and $s.HeartbeatFresh

    if ($everythingOk) {
        $script:ConsecutiveFailures = 0
        $script:ComponentAttempts   = @{}
        $script:ActionBackoff       = 0
        $script:MaintenanceLogged   = $false
        Write-Heartbeat -Status "ok" -Stack $s.Summary
        return
    }

    $script:ConsecutiveFailures++
    $n = $script:ConsecutiveFailures

    # ---- Tier 1: defer - a live launcher is already fixing it --------------
    $working = $s.Status -in @("starting", "recovering", "restarting_gateway",
                               "restarting_frontend", "degraded", "starting_adopting")
    $launcherUptime = [double]::MaxValue
    if ($s.LauncherAlive) {
        try {
            $lp = Get-PidFileValue -Path $AlphaPid
            $launcherUptime = ([DateTime]::UtcNow - (Get-Process -Id $lp).StartTime.ToUniversalTime()).TotalSeconds
        } catch {}
    }
    # Tier 1 defer: a live, heartbeating launcher in a working status owns
    # recovery at any normal uptime. The old gate (uptime < 600 s) let this
    # loop start killing port trees the launcher was still booting once a
    # slow cold boot passed 10 minutes, which cascaded into endless full
    # cold restarts. The 3x cap still bounds a wedged launcher: past it we
    # stop deferring and escalate (component attempts -> full stack replace).
    $workingDeferCap = $launcherUptime -lt ($StartupGraceSeconds * 3)

    if ($s.LauncherAlive -and $s.HeartbeatFresh) {
        if ($working -and $workingDeferCap) {
            Write-Heartbeat -Status "deferring" -Stack $s.Summary
            if ($n -eq 1 -or ($n % 4 -eq 0)) {
                Write-WdLog "Deferring recovery x${n}: launcher status=$($s.Status) uptime=$([int]$launcherUptime)s is fixing it ($($s.Summary))"
            }
            return
        }
        if ($n -le $MaxDeferredRecoveries -and $s.Status -in @("healthy", "running", "degraded", "unknown")) {
            Write-Heartbeat -Status "deferring" -Stack $s.Summary
            if ($n -eq 1) {
                Write-WdLog "Deferring recovery x${n}: launcher alive (status=$($s.Status)), letting its 2 s monitor act ($($s.Summary))"
            }
            return
        }
    }

    # Cooldown between recovery actions - never spin at full speed.
    if ($script:LastActionUtc -and $script:ActionBackoff -gt 0) {
        $sinceAction = ([DateTime]::UtcNow - $script:LastActionUtc).TotalSeconds
        if ($sinceAction -lt $script:ActionBackoff) {
            Write-Heartbeat -Status "cooldown" -Stack $s.Summary
            return
        }
    }

    # ---- Tier 2: component restart (smallest safe recovery) ----------------
    $badComponent = $null
    if ($s.GatewayState -in @("down", "hung")) { $badComponent = "gateway" }
    elseif ($s.FrontendState -in @("down", "hung")) { $badComponent = "frontend" }

    if ($badComponent -and $s.LauncherAlive -and $s.HeartbeatFresh) {
        $attempts = 0
        if ($script:ComponentAttempts.ContainsKey($badComponent)) {
            $attempts = $script:ComponentAttempts[$badComponent]
        }
        if ($attempts -lt $MaxComponentRecoveries) {
            $script:ComponentAttempts[$badComponent] = $attempts + 1
            Write-Heartbeat -Status "recovering" -Stack $s.Summary
            $script:ConsecutiveFailures = 0
            $script:LastActionUtc = [DateTime]::UtcNow
            $script:ActionBackoff = 15
            Restart-Component -Component $badComponent | Out-Null
            return
        }
        Write-WdLog "$badComponent exceeded $MaxComponentRecoveries component restarts - escalating to full stack" "WARN"
    }

    # ---- Tier 3: full stack restart ---------------------------------------
    Write-Heartbeat -Status "recovering" -Stack $s.Summary
    $reason = "escalation after $n failing checks: $($s.Summary)"
    $ok = Start-AlphaStack -Reason $reason
    $script:ConsecutiveFailures = 0
    $script:ComponentAttempts   = @{}
    $script:LastActionUtc = [DateTime]::UtcNow
    if ($script:ActionBackoff -eq 0) { $script:ActionBackoff = $ActionBackoffStart }
    else { $script:ActionBackoff = [Math]::Min($ActionBackoffCap, $script:ActionBackoff * 2) }
    if (-not $ok) { Write-WdLog "Full restart spawn failed - will retry after backoff" "ERROR" }
}

# ----------------------------------------------------------- Layer 4: -Once ---
# Watchdog-of-watchdog. This does NOT depend on Alpha being healthy: it only
# verifies that a correct, fresh Layer 3 loop exists for this installation and
# recreates it when it does not. The loop then owns recovery decisions.
function Invoke-WatchdogOfWatchdog {
    if (Test-Maintenance) {
        if (-not $script:MaintenanceLogged) {
            Write-WdLog "Maintenance mode - Layer 4 pass standing down (no loop start, no recovery)"
        }
        return
    }

    $owner = Get-PidFileValue -Path $WatchdogPid
    $loopOk = $false
    if ($owner -gt 0) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($proc -and (Test-WatchdogHeartbeatFresh -ExpectedPid $owner)) {
            # Must also be monitoring THIS installation, not another checkout.
            $cmdline = ""
            try {
                $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction Stop).CommandLine
            } catch {}
            if ($cmdline -like "*$WatchdogScript*") { $loopOk = $true }
            else { Write-WdLog "Watchdog PID $owner is not monitoring this installation ($cmdline) - replacing" "WARN" }
        } else {
            Write-WdLog "Watchdog loop unhealthy: pid=$owner alive=$([bool]$proc) heartbeat_fresh=$(Test-WatchdogHeartbeatFresh -ExpectedPid $owner) - repairing" "WARN"
        }
    } else {
        Write-WdLog "No watchdog loop registered - starting one"
    }

    if ($loopOk) {
        $snap = ""
        try { $snap = (Get-StackSnapshot).Summary } catch {}
        Write-WdLog "Watchdog loop healthy (PID $owner); $snap"
        return
    }

    # Remove stale loop remnants, then recreate detached.
    foreach ($pf in @($WatchdogPid)) {
        $stalePid = Get-PidFileValue -Path $pf
        if ($stalePid -gt 0 -and $stalePid -ne $PID -and (Get-Process -Id $stalePid -ErrorAction SilentlyContinue)) {
            & taskkill /PID $stalePid /T /F 2>&1 | Out-Null
            Start-Sleep -Seconds 1
        }
        Remove-Item $pf -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $WatchdogHeartbeat -Force -ErrorAction SilentlyContinue

    $shimCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WatchdogScript`""
    $shim = Write-Shim -Name "alpha_watchdog_shim.vbs" -CommandLine $shimCmd
    if (Invoke-Detached -ShimPath $shim) {
        Write-RecoveryEvent -Component "watchdog" -Action "loop_recreate" -Result "spawned" -Reason "Layer 4 supervision pass"
    } else {
        Write-RecoveryEvent -Component "watchdog" -Action "loop_recreate" -Result "spawn_failed" -Reason "Layer 4 supervision pass"
    }
}

# ------------------------------------------------------------------- stop -----
function Stop-WatchdogLoop {
    $lp = Get-PidFileValue -Path $WatchdogPid
    if ($lp -gt 0 -and $lp -ne $PID -and (Get-Process -Id $lp -ErrorAction SilentlyContinue)) {
        & taskkill /PID $lp /T /F 2>&1 | Out-Null
        Write-WdLog "Stopped watchdog loop (PID $lp) on request"
    }
    Remove-Item $WatchdogPid -Force -ErrorAction SilentlyContinue
    Remove-Item $WatchdogHeartbeat -Force -ErrorAction SilentlyContinue
    if ($lp -eq $PID) { exit 0 }
}

if ($Stop) { Stop-WatchdogLoop; Write-Host "Watchdog loop stopped."; exit 0 }

# ------------------------------------------------- Layer 3 loop entry point ---
if ($Once -or $StartIfDown) {
    Invoke-WatchdogOfWatchdog
    exit 0
}

# ---- Duplicate-instance guard: exactly one loop may own the role -------------
$existing = Get-PidFileValue -Path $WatchdogPid
if ($existing -gt 0 -and $existing -ne $PID) {
    $proc = Get-Process -Id $existing -ErrorAction SilentlyContinue
    if ($proc -and (Test-WatchdogHeartbeatFresh -ExpectedPid $existing)) {
        Write-WdLog "Another watchdog (PID $existing) already owns the loop - duplicate exiting"
        exit 0
    }
    if ($proc) {
        Write-WdLog "Replacing frozen watchdog PID $existing" "WARN"
        & taskkill /PID $existing /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    }
}
Write-StateFile -Path $WatchdogPid -Object ([int]$PID)
Write-Heartbeat -Status "starting"
Write-WdLog "Watchdog loop started (PID $PID, interval ${CheckIntervalSeconds}s)"

while ($true) {
    try {
        # Ownership: if another loop claimed the role, stand down.
        $owner = Get-PidFileValue -Path $WatchdogPid
        if ($owner -eq 0) { Write-StateFile -Path $WatchdogPid -Object ([int]$PID) }
        elseif ($owner -ne $PID) {
            Write-WdLog "Loop role taken over by PID $owner - exiting duplicate" "WARN"
            break
        }
        if (Test-Maintenance) {
            if (-not $script:MaintenanceLogged) {
                Write-WdLog "Maintenance mode detected - watchdog standing down until .\start.ps1 resumes"
                $script:MaintenanceLogged = $true
            }
            break
        }
        Invoke-HealthCheck
    } catch {
        Write-WdLog "Watchdog check error: $($_.Exception.Message)" "ERROR"
        try { Write-Heartbeat -Status "degraded" } catch {}
    }
    Start-Sleep -Seconds $CheckIntervalSeconds
}

# Cleanup only what we own.
$owner = Get-PidFileValue -Path $WatchdogPid
if ($owner -eq $PID -or $owner -eq 0) {
    Remove-Item $WatchdogPid -Force -ErrorAction SilentlyContinue
    if (Test-Path $WatchdogHeartbeat) {
        try {
            $hbPid = [int](Get-Content $WatchdogHeartbeat -Raw | ConvertFrom-Json).pid
            if ($hbPid -eq $PID) { Remove-Item $WatchdogHeartbeat -Force -ErrorAction SilentlyContinue }
        } catch {}
    }
}
Write-WdLog "Watchdog loop (PID $PID) exiting"
