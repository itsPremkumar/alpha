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
#   .\recovery\watchdog.ps1             # Start watchdog loop (blocks)
#   .\recovery\watchdog.ps1 -Once       # Layer 4 pass: verify/recreate the loop
#   .\recovery\watchdog.ps1 -StartIfDown# Alias of -Once (logon trigger)
#   .\recovery\watchdog.ps1 -Stop       # Stop the running loop

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
$WatchdogScript   = "$RepoRoot\recovery\watchdog.ps1"
$GatewayPort      = 8001
$FrontendPort     = 3000

$CheckIntervalSeconds     = 30
$WatchdogHeartbeatMaxAge  = 90    # loop heartbeat older than this = frozen loop
$LauncherHeartbeatMaxAge  = 360   # start.ps1 can legitimately pause up to 300 s in backoff
$StartupGraceSeconds      = 600   # a launcher younger than this is still in first boot
$MaxDeferredRecoveries    = 8     # checks we let a live launcher heal itself first
$MaxComponentRecoveries   = 3     # per-component restarts before escalating to the stack
# A supervisor must be MORE patient than the process it supervises, otherwise it
# kills a component that is still legitimately booting. start.ps1 waits
# `MaxWaitSeconds 240` for the gateway and `360` for the frontend before it
# declares defeat and retries; these thresholds must therefore stay strictly
# ABOVE those budgets (threshold x $CheckIntervalSeconds > budget). When the
# gateway's was 6 (180 s) the watchdog killed a booting gateway 60 s before the
# launcher would have accepted it, so on a loaded machine the stack could never
# converge and the UI sat at "starting ... waiting for services" forever.
# Pinned by backend/tests/test_launcher_watchdog_budget.py.
$GatewayHungThreshold     = 10    # 300 s > start.ps1's 240 s gateway budget
$FrontendHungThreshold    = 45    # 1350 s > start.ps1's 1200 s frontend budget
$ActionBackoffStart       = 30    # full-stack restart backoff (doubles, capped)
$ActionBackoffCap         = 300

$script:WatchdogStartedUtc = [DateTime]::UtcNow.ToString("o")
$script:GwHttpFail   = 0
$script:FeHttpFail   = 0
$script:ConsecutiveFailures = 0
# Logging-only counter: how many passes chose to defer. Deferrals are not
# failures, so the working-tier defer in Invoke-HealthCheck RESETS
# ConsecutiveFailures instead of feeding it, and this counter is what
# "Deferring recovery x24" counts. It resets whenever the stack is OK or a
# recovery action is taken.
$script:DeferPasses  = 0
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

# True when a log file was written to within $WithinSeconds.
#
# This is the progress signal that keeps the watchdog from killing a component
# that is still legitimately working. Next.js binds its port BEFORE it finishes
# compiling, so "port open + HTTP not answering" is ambiguous: it is what a
# wedged process looks like AND what a 14-minute cold compile looks like. A
# supervisor must not kill a process that is demonstrably still advancing, and
# restarting a compiling frontend throws away the whole compile.
function Test-FileRecentlyWritten {
    param([string]$Path, [int]$WithinSeconds = 120)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    try {
        $written = (Get-Item $Path -ErrorAction Stop).LastWriteTime
        return (((Get-Date) - $written).TotalSeconds -lt $WithinSeconds)
    } catch { return $false }
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

# --------------------------------------------------------------- supervisor lock ---
# Only ONE supervisor may act on the stack.
#
# A persistent watchdog loop and a `watchdog.ps1 -Once` pass can both observe the
# same unhealthy stack, and both can escalate independently. Each escalation
# spawns a launcher, and a launcher's startup clears ports 8001/3000, so the two
# supervisors tear the stack down and rebuild it in a loop. Measured on this
# machine: `launcher=dead` / `launcher=alive` flip-flopping every ~40 s with the
# frontend never finishing its compile.
#
# An exclusive open is used rather than a PID file because the OS releases it
# when the holder exits, so a crashed supervisor cannot leave a lock that blocks
# recovery forever. A second supervisor that cannot take the lock WAITS - it
# never acts, and never kills. This mirrors a file-lock coordinator
# (src/infra/gateway-lock.ts) rather than kill-then-start.
$script:SupervisorLockStream = $null

function Enter-SupervisorLock {
    if ($script:SupervisorLockStream) { return $true }
    $path = "$LogDir\watchdog.lock"
    try {
        # FileShare.None: the kernel refuses a concurrent open and drops the
        # handle automatically if this process dies.
        $fs = [System.IO.File]::Open(
            $path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $payload = [System.Text.Encoding]::UTF8.GetBytes("pid=$PID acquired=$([DateTime]::UtcNow.ToString('o'))")
        $fs.Write($payload, 0, $payload.Length)
        $fs.Flush()
        $script:SupervisorLockStream = $fs
        return $true
    } catch {
        return $false
    }
}

function Exit-SupervisorLock {
    if (-not $script:SupervisorLockStream) { return }
    try { $script:SupervisorLockStream.Close() } catch { }
    $script:SupervisorLockStream = $null
}

function Stop-StaleLauncher {
    # The PID file records only the MOST RECENT launcher. A launcher that
    # started before the file was last written, or one whose entry a competing
    # launcher has already overwritten, is invisible to a PID-file-only sweep.
    #
    # Two live launchers are fatal, not merely wasteful: each one's startup
    # clears whatever holds ports 8001/3000, so they alternately kill each
    # other's gateway and neither ever finishes binding. Measured on this
    # machine: launchers 4140 and 15500 both alive and both running a gateway,
    # while logs\alpha.pid named only 15500 - so nothing would ever reap 4140,
    # port 8001 was never stably bound, and the tray sat at
    # "waiting for services (gateway=False frontend=False)".
    #
    # So: stop the recorded PID *and* discover every other live launcher by
    # command line. The watchdog itself is excluded, or it would kill itself.
    $targets = @()

    $lp = Get-PidFileValue -Path $AlphaPid
    if ($lp -gt 0 -and $lp -ne $PID) { $targets += $lp }

    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -in @("powershell.exe", "pwsh.exe") })
        foreach ($p in $procs) {
            $pid2 = [int]$p.ProcessId
            if ($pid2 -le 0 -or $pid2 -eq $PID) { continue }
            $cmd = [string]$p.CommandLine
            if (-not $cmd) { continue }
            # This watchdog runs from watchdog.ps1; never target ourselves.
            if ($cmd -match 'watchdog\.ps1') { continue }
            if ($cmd -like "*$StartScript*") { $targets += $pid2 }
        }
    } catch { }

    $targets = @($targets | Where-Object { $_ -gt 0 -and $_ -ne $PID } | Select-Object -Unique)
    if ($targets.Count -eq 0) { return }

    foreach ($id in $targets) {
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            & taskkill /PID $id /T /F 2>&1 | Out-Null
        }
    }
    Start-Sleep -Seconds 1
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
    # Serve the frontend from a production build when one exists.
    #
    # `next dev` binds :3000 and then cold-compiles: measured at 880 s (1710
    # modules) on this machine, versus "Ready in 43.7s" for a `next start` that
    # has a build to serve. That compile is the single largest cost in a cold
    # start and it is paid again on every boot. start.ps1 already implements the
    # -Prod path (it builds once if .next/BUILD_ID is missing, then runs
    # `next start`), so the watchdog just has to ask for it.
    #
    # Auto-detected rather than forced, so editing components still gets HMR: no
    # build present means the dev server, exactly as before. The mode in force is
    # written to the heartbeat so the choice is never silent.
    $prodFlag = ""
    if (Test-Path "$RepoRoot\frontend\.next\BUILD_ID") { $prodFlag = " -Prod" }
    $shimCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -NoBrowser -WatchdogMode$prodFlag"
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
        } elseif (Test-FileRecentlyWritten -Path "$LogDir\frontend.log" -WithinSeconds 120) {
            # Next.js binds the port before it finishes compiling, and this app's
            # cold compile of "/" measured 880 s (1710 modules) on a loaded
            # machine. A frontend whose build log is still advancing is making
            # progress, not hung. Treating it as hung restarted the process and
            # reset the compile, which is what pinned the tray at
            # "starting ... waiting for services (gateway=True frontend=False)"
            # through launcher attempt 189/450.
            $script:FeHttpFail = 0; $fe = "compiling"
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
        $script:DeferPasses         = 0
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
            # A deferral is a decision NOT to act: it must not feed the
            # escalation counter, or a long working period builds a huge n and
            # the FIRST healthy-status check after it fails the n<=8 budget in
            # the next branch and escalates to a full stack restart instantly
            # (measured: "escalation after 28 failing checks ... status=healthy").
            # $script:DeferPasses is what those passes count against now.
            $script:ConsecutiveFailures = 0
            $script:DeferPasses++
            Write-Heartbeat -Status "deferring" -Stack $s.Summary
            if ($script:DeferPasses -eq 1 -or ($script:DeferPasses % 4 -eq 0)) {
                Write-WdLog "Deferring recovery x$($script:DeferPasses): launcher status=$($s.Status) uptime=$([int]$launcherUptime)s is fixing it ($($s.Summary))"
            }
            return
        }
        if ($n -le $MaxDeferredRecoveries -and $s.Status -in @("healthy", "running", "degraded", "unknown")) {
            # This branch DELIBERATELY lets $n grow: it is the launcher's
            # budget (n checks on a "healthy" claim before we act), and it is
            # only reachable with a small n now that the working defer above
            # resets the counter instead of feeding it.
            $script:DeferPasses++
            Write-Heartbeat -Status "deferring" -Stack $s.Summary
            if ($n -eq 1) {
                Write-WdLog "Deferring recovery x${n}: launcher alive (status=$($s.Status)), letting its 2 s monitor act ($($s.Summary))"
            }
            return
        }
    }

    # ---- Supervisor exclusivity -------------------------------------------------
    # Everything below this line DESTROYS or RESPAWNS something. Only the process
    # holding the supervisor lock may do that; a second watchdog that merely
    # *observes* an unhealthy stack must wait instead of racing the first one.
    if (-not (Enter-SupervisorLock)) {
        Write-Heartbeat -Status "deferring" -Stack $s.Summary
        if ($n -eq 1 -or ($n % 8 -eq 0)) {
            Write-WdLog "Another supervisor holds $LogDir\watchdog.lock - observing only, not acting ($($s.Summary))"
        }
        return
    }

    # Cooldown between recovery actions - never spin at full speed.
    if ($script:LastActionUtc -and $script:ActionBackoff -gt 0) {
        $sinceAction = ([DateTime]::UtcNow - $script:LastActionUtc).TotalSeconds
        if ($sinceAction -lt $script:ActionBackoff) {
            # NOTE: `return` must be on its own line - PowerShell parses a
            # trailing bare word on the same line as another command as a
            # positional ARGUMENT to that command, so the merged form below
            # never returned and the cooldown pass fell through to Tier 2/3
            # and destroyed a stack it was supposed to leave alone:
            #   Write-Heartbeat -Status "cooldown" -Stack $s.Summary   return
            Write-Heartbeat -Status "cooldown" -Stack $s.Summary
            return
        }
    }

    # ---- Tier 2: component restart (smallest safe recovery) ----------------
    $badComponent = $null
    if ($s.GatewayState -in @("down", "hung")) { $badComponent = "gateway" }
    elseif ($s.FrontendState -in @("down", "hung")) { $badComponent = "frontend" }
    $tier2Exhausted = $false

    if ($badComponent -and $s.LauncherAlive -and $s.HeartbeatFresh) {
        $attempts = 0
        if ($script:ComponentAttempts.ContainsKey($badComponent)) {
            $attempts = $script:ComponentAttempts[$badComponent]
        }
        if ($attempts -lt $MaxComponentRecoveries) {
            $script:ComponentAttempts[$badComponent] = $attempts + 1
            Write-Heartbeat -Status "recovering" -Stack $s.Summary
            $script:ConsecutiveFailures = 0
            $script:DeferPasses = 0     # an action ends the deferral streak (see the config comment)
            $script:LastActionUtc = [DateTime]::UtcNow
            $script:ActionBackoff = 15
            Restart-Component -Component $badComponent | Out-Null
            return
        }
        $tier2Exhausted = $true
        Write-WdLog "$badComponent exceeded $MaxComponentRecoveries component restarts - escalating to full stack" "WARN"
    }

    # ---- Tier 3: full stack restart ---------------------------------------
    # GATE: a full restart DESTROYS the whole tree, so it is only justified
    # when nobody else can fix the stack:
    #   * the launcher is dead or its heartbeat is stale (it cannot act), or
    #   * a component is down/hung AND its Tier 2 budget is exhausted.
    # Anything else - notably a live launcher still mid-boot with the gateway
    # merely `starting` - is deferred instead of being killed mid-restart.
    # That is the measured restart loop: "escalation after 28 failing checks:
    # gateway=starting ... launcher=alive launcher_hb=fresh status=healthy".
    # `starting`/`compiling` cannot persist past the hung thresholds (the
    # http-fail counters only reset on success or port close), so a deferred
    # state always resolves itself into `up` or into a Tier 2-eligible
    # `down`/`hung` - the gate cannot create a permanent no-action state.
    if ($s.LauncherAlive -and $s.HeartbeatFresh -and -not $tier2Exhausted) {
        # Deferral is a decision NOT to act: it must not feed the escalation
        # counter (same rule as Tier 1), and it must give the supervisor lock
        # back so a pass that needs to act is not blocked by this one.
        $script:ConsecutiveFailures = 0
        $script:DeferPasses++
        Write-Heartbeat -Status "deferring" -Stack $s.Summary
        if ($script:DeferPasses -eq 1 -or ($script:DeferPasses % 4 -eq 0)) {
            Write-WdLog "Deferring full-stack restart x$($script:DeferPasses): launcher alive and no component exhausted Tier 2 ($($s.Summary))"
        }
        Exit-SupervisorLock
        return
    }
    Write-Heartbeat -Status "recovering" -Stack $s.Summary
    $reason = "escalation after $n failing checks: $($s.Summary)"
    $ok = Start-AlphaStack -Reason $reason
    $script:ConsecutiveFailures = 0
    $script:DeferPasses         = 0     # an action ends the deferral streak (see the config comment)
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
