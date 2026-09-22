# Alpha lifecycle & recovery verification
#
# Actually executes failure injection and asserts automatic recovery. Prints one
# PASS/FAIL line per scenario plus a summary, and exits non-zero if anything
# failed. This is a real test: it kills live processes and waits for the
# recovery chain (watchdog -> launcher -> services) to restore them.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\verify_recovery.ps1
#   ... -IncludeMaintenance      # also test stop.ps1 maintenance + resume
#   ... -IncludeProvider        # also test gateway boot with an empty API key
#   ... -SkipStackKills         # state/task/health checks only (fast)

[CmdletBinding()]
param(
    [int]$ServiceTimeout = 420,     # cold gateway needs ~180-240 s
    [switch]$IncludeMaintenance,
    [switch]$IncludeProvider,
    [switch]$SkipStackKills
)

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot
$GatewayPort = 8001
$FrontendPort = 3000

$script:Results = New-Object System.Collections.ArrayList
$script:LogDir = "$RepoRoot\logs"

function Write-Step([string]$msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Record([string]$Name, [bool]$Pass, [string]$Detail = "") {
    $status = if ($Pass) { "PASS" } else { "FAIL" }
    $color = if ($Pass) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1}{2}" -f $status, $Name, $(if ($Detail) { " - $Detail" } else { "" })) -ForegroundColor $color
    [void]$script:Results.Add([pscustomobject]@{ Scenario = $Name; Result = $status; Detail = $Detail })
}

function Get-PortPid([int]$Port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
    } catch {}
    return 0
}
function Test-GwUp {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$GatewayPort/health/ready" -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
function Test-FeUp {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/" -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
function Test-StackHealthy { return ((Test-GwUp) -and (Test-FeUp)) }
function Wait-StackHealthy([int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-StackHealthy) { return $true }
        Start-Sleep -Seconds 10
    }
    return (Test-StackHealthy)
}
function Get-LauncherPid {
    try { return [int](Get-Content "$LogDir\alpha.pid" -Raw -ErrorAction Stop) } catch { return 0 }
}
function Get-WatchdogPid {
    try { return [int](Get-Content "$LogDir\watchdog.pid" -Raw -ErrorAction Stop) } catch { return 0 }
}
function Get-HealthStatus {
    try { return [string](Get-Content "$LogDir\alpha_health.json" -Raw | ConvertFrom-Json).status } catch { return "" }
}
function Test-MaintenanceFlag { return (Test-Path "$LogDir\alpha_maintenance.json") }

# Runs a script block and measures how long until it returns true.
function Measure-Recovery([scriptblock]$Condition, [int]$TimeoutSec) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { $sw.Stop(); return [pscustomobject]@{ Ok = $true; Seconds = [int]$sw.Elapsed.TotalSeconds } }
        Start-Sleep -Seconds 5
    }
    $sw.Stop()
    return [pscustomobject]@{ Ok = $false; Seconds = [int]$sw.Elapsed.TotalSeconds }
}

# ---------------------------------------------------------------- 1. state ---
Write-Step "Scenario group 1: state files, health vocabulary, scheduled tasks"

$healthOk = $false; $healthStatus = ""
try {
    $h = Get-Content "$LogDir\alpha_health.json" -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $healthStatus = [string]$h.status
    $healthOk = ($h.status -in @('starting', 'healthy', 'degraded', 'recovering', 'failed')) -and ($h.pid -gt 0) -and $h.launcher_started_utc
} catch {}
Record "Health file parses with valid status vocabulary" $healthOk "status=$healthStatus"

$hbOk = $false; $hbDetail = ""
try {
    $hb = Get-Content "$LogDir\watchdog_heartbeat.json" -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $age = ([DateTime]::UtcNow - [DateTime]::Parse($hb.timestamp_utc, $null,
        [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()).TotalSeconds
    $hbOk = ($age -ge 0 -and $age -le 90) -and ([int]$hb.pid -gt 0) -and $hb.component -eq 'watchdog'
    $hbDetail = "age=$([int]$age)s pid=$($hb.pid)"
} catch { $hbDetail = $_.Exception.Message }
Record "Watchdog heartbeat file fresh (<=90s)" $hbOk $hbDetail

foreach ($tn in @('Alpha_Autostart', 'Alpha_Watchdog')) {
    $t = $null
    try { $t = Get-ScheduledTask -TaskName $tn -ErrorAction Stop } catch {}
    $ok = $false; $d = ""
    if ($t) {
        $info = $null
        try { $info = Get-ScheduledTaskInfo -TaskName $tn -ErrorAction Stop } catch {}
        $ok = ($t.State -eq 'Ready' -or $t.State -eq 'Running')
        $d = "state=$($t.State) lastResult=$($info.LastTaskResult) next=$($info.NextRunTime)"
    } else { $d = "task missing" }
    Record "Scheduled task $tn registered and enabled" $ok $d
}

$launcherPid = Get-LauncherPid
$launcherAlive = ($launcherPid -gt 0 -and (Get-Process -Id $launcherPid -ErrorAction SilentlyContinue))
Record "Launcher process alive" $launcherAlive "pid=$launcherPid"

$wdPid = Get-WatchdogPid
$wdAlive = ($wdPid -gt 0 -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue))
Record "Watchdog loop process alive" $wdAlive "pid=$wdPid"

Record "Stack healthy (gateway /health/ready + frontend /)" (Test-StackHealthy) "gw=$(Test-GwUp) fe=$(Test-FeUp)"

if (-not $SkipStackKills) {

    # ---------------------------------------------------- 2. gateway crash ---
    Write-Step "Scenario 2: kill the gateway, expect automatic recovery"
    $gpid = Get-PortPid $GatewayPort
    if ($gpid -gt 0) {
        & taskkill /PID $gpid /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 5
        $down = -not (Test-GwUp)
        Record "Gateway actually went down after taskkill" $down "killed pid=$gpid"
        $r = Measure-Recovery { (Test-GwUp) } $ServiceTimeout
        Record "Gateway recovered automatically" $r.Ok "recovered in $($r.Seconds)s"
        Record "Launcher survived a component crash" ((Get-LauncherPid) -eq $launcherPid -or (Get-Process -Id (Get-LauncherPid) -ErrorAction SilentlyContinue)) "launcher=$(Get-LauncherPid)"
    } else {
        Record "Gateway kill precondition (gateway running)" $false "port $GatewayPort not listening"
    }

    # -------------------------------------------------- 3. frontend crash ----
    Write-Step "Scenario 3: kill the frontend, expect automatic recovery"
    $fpid = Get-PortPid $FrontendPort
    if ($fpid -gt 0) {
        & taskkill /PID $fpid /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 5
        $down = -not (Test-FeUp)
        Record "Frontend actually went down after taskkill" $down "killed pid=$fpid"
        $r = Measure-Recovery { (Test-FeUp) } $ServiceTimeout
        Record "Frontend recovered automatically" $r.Ok "recovered in $($r.Seconds)s"
    } else {
        Record "Frontend kill precondition (frontend running)" $false "port $FrontendPort not listening"
    }

    # --------------------------------------------------- 4. launcher crash ---
    Write-Step "Scenario 4: kill the launcher, expect watchdog to replace it"
    $lp = Get-LauncherPid
    if ($lp -gt 0 -and (Get-Process -Id $lp -ErrorAction SilentlyContinue)) {
        & taskkill /PID $lp /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 5
        Record "Launcher actually died" (-not (Get-Process -Id $lp -ErrorAction SilentlyContinue)) "killed pid=$lp"
        # Killing the launcher takes its child services down with it, so this
        # recovery = watchdog replacement (<=~120 s) + a FULL cold stack boot.
        # That legitimately needs more than a single service-restart budget.
        $r = Measure-Recovery {
            $nl = Get-LauncherPid
            ($nl -gt 0 -and $nl -ne $lp -and (Get-Process -Id $nl -ErrorAction SilentlyContinue)) -and (Test-StackHealthy)
        } ($ServiceTimeout + 180)
        Record "Watchdog replaced the launcher and stack is healthy" $r.Ok "recovered in $($r.Seconds)s; new launcher=$(Get-LauncherPid)"
    } else {
        Record "Launcher kill precondition (launcher running)" $false "pid=$lp"
    }

    # --------------------------------------------------- 5. watchdog crash ---
    Write-Step "Scenario 5: kill the watchdog, Alpha must stay up, Layer 4 must restore it"
    # Precondition: without a healthy stack, "Alpha stayed up" would just
    # re-report scenario 4's leftover state instead of testing detachment.
    $preOk = Wait-StackHealthy -TimeoutSec $ServiceTimeout
    Record "Stack healthy before watchdog kill (precondition)" $preOk "gw=$(Test-GwUp) fe=$(Test-FeUp)"
    $wp = Get-WatchdogPid
    if (-not $preOk) {
        Record "Watchdog kill scenario ran" $false "skipped: stack never became healthy"
    } elseif ($wp -gt 0 -and (Get-Process -Id $wp -ErrorAction SilentlyContinue)) {
        & taskkill /PID $wp /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 5
        Record "Watchdog loop actually died" (-not (Get-Process -Id $wp -ErrorAction SilentlyContinue)) "killed pid=$wp"
        # Detachment check: killing Layer 3 must NOT take Layer 2/1 with it.
        Record "Alpha stayed up while watchdog was dead" ((Test-GwUp) -and (Test-FeUp)) "gw=$(Test-GwUp) fe=$(Test-FeUp)"
        # Layer 4 supervision pass (what Alpha_Watchdog runs every 5 minutes).
        & powershell -NoProfile -ExecutionPolicy Bypass -File "$RepoRoot\scripts\watchdog.ps1" -Once 2>&1 | Out-Null
        $r = Measure-Recovery { $n = Get-WatchdogPid; ($n -gt 0 -and $n -ne $wp -and (Get-Process -Id $n -ErrorAction SilentlyContinue)) } 60
        Record "Layer 4 (-Once) recreated the watchdog loop" $r.Ok "new pid=$(Get-WatchdogPid)"
        Record "Alpha still healthy after watchdog recreation" (Test-StackHealthy) ""
    } else {
        Record "Watchdog kill precondition (loop running)" $false "pid=$wp"
    }

    # ------------------------------------------------- 6. double crash --------
    Write-Step "Scenario 6: kill gateway + frontend simultaneously"
    $preOk = Wait-StackHealthy -TimeoutSec $ServiceTimeout
    Record "Stack healthy before double kill (precondition)" $preOk $script:WaitDetail
    foreach ($port in @($GatewayPort, $FrontendPort)) {
        $pid1 = Get-PortPid $port
        if ($pid1 -gt 0) { & taskkill /PID $pid1 /T /F 2>&1 | Out-Null }
    }
    Start-Sleep -Seconds 5
    # A double kill forces a cold Next.js compile (~281 s) plus a cold
    # gateway boot in parallel; give it the launcher-replacement budget too.
    $r = Measure-Recovery { (Test-StackHealthy) } ($ServiceTimeout + 180)
    Record "Full stack recovered from simultaneous double kill" $r.Ok "recovered in $($r.Seconds)s"
}

# --------------------------------------------------------- 7. maintenance ----
if ($IncludeMaintenance -and -not $SkipStackKills) {
    Write-Step "Scenario 7: intentional stop (maintenance) stays stopped, start resumes"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$RepoRoot\stop.ps1" 2>&1 | Out-Null
    Start-Sleep -Seconds 8
    Record "stop.ps1 wrote the maintenance flag" (Test-MaintenanceFlag) ""
    Record "stop.ps1 stopped the services" ((-not (Test-GwUp)) -and (-not (Test-FeUp))) ""
    # NB: pid 0 (file removed) must count as "gone" - Get-Process -Id 0
    # matches System Idle Process and would fake a failure here.
    $wdPid = Get-WatchdogPid
    $wdGone = ($wdPid -le 0) -or -not (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)
    Record "stop.ps1 stopped the watchdog loop" $wdGone "pid=$wdPid"

    # Layer 4 must respect maintenance instead of resurrecting Alpha.
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$RepoRoot\scripts\watchdog.ps1" -Once 2>&1 | Out-Null
    Start-Sleep -Seconds 10
    $wdPid = Get-WatchdogPid
    Record "Layer 4 respected maintenance (no loop recreated)" (($wdPid -le 0) -or -not (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) "pid=$wdPid"
    Record "Alpha stayed down during maintenance" ((-not (Test-GwUp)) -and (-not (Test-FeUp))) ""

    # Resume: start.ps1 clears the flag and boots the stack.
    # NB: embed quotes around -File's path: Start-Process joins the array
    # WITHOUT quoting elements, so an unquoted path containing spaces (e.g.
    # C:\Users\PREM KUMAR\...) is truncated at the first space and start.ps1
    # silently never runs - that is exactly what broke the resume below.
    Start-Process -FilePath "powershell.exe" -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', "`"$RepoRoot\start.ps1`"", '-NoBrowser', '-WatchdogMode') -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
    $r = Measure-Recovery { (-not (Test-MaintenanceFlag)) -and (Test-StackHealthy) } $ServiceTimeout
    # Detail shows WHICH half failed: flag still set (start died early) vs
    # services not yet healthy (cold boot budget).
    Record "start.ps1 cleared maintenance and brought the stack up" $r.Ok "recovered in $($r.Seconds)s flag=$([bool](Test-MaintenanceFlag)) gw=$(Test-GwUp) fe=$(Test-FeUp)"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$RepoRoot\scripts\watchdog.ps1" -Once 2>&1 | Out-Null
    Start-Sleep -Seconds 10
    $wdPid = Get-WatchdogPid
    Record "Watchdog loop restored after resume" (($wdPid -gt 0) -and (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) "pid=$wdPid flag=$([bool](Test-MaintenanceFlag))"
}

# ------------------------------------------------------ 8. LLM independence --
if ($IncludeProvider) {
    Write-Step "Scenario 8: gateway must boot healthy with NO LLM API key"
    $scratchPort = 8011
    $oldPid = Get-PortPid $scratchPort
    if ($oldPid -gt 0) { & taskkill /PID $oldPid /T /F 2>&1 | Out-Null; Start-Sleep -Seconds 5 }
    # Empty existing env var wins over .env (python-dotenv does not override),
    # so the gateway sees a missing provider key exactly as a user without one.
    $psi = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
            "`$env:OPENROUTER_API_KEY=''; & uv run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port $scratchPort") `
        -WorkingDirectory "$RepoRoot\backend" -WindowStyle Hidden `
        -RedirectStandardOutput "$LogDir\provider_test.out.log" -RedirectStandardError "$LogDir\provider_test.err.log" -PassThru
    $deadline = (Get-Date).AddSeconds(300)
    $gwNoKey = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$scratchPort/health/ready" -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $gwNoKey = $true; break }
        } catch {}
        if ($psi.HasExited) { break }
        Start-Sleep -Seconds 10
    }
    Record "Gateway healthy with empty OPENROUTER_API_KEY (infra != provider)" $gwNoKey "port=$scratchPort exited=$($psi.HasExited)"
    # Real stack must be unaffected by the provider experiment.
    Record "Main stack unaffected by provider test" (Test-StackHealthy) ""
    if ($psi -and -not $psi.HasExited) { & taskkill /PID $psi.Id /T /F 2>&1 | Out-Null }
    $sp = Get-PortPid $scratchPort
    if ($sp -gt 0) { & taskkill /PID $sp /T /F 2>&1 | Out-Null }
}

# ------------------------------------------------------------- 9. report -----
Write-Host "`n================= VERIFICATION SUMMARY =================" -ForegroundColor White
$pass = @($script:Results | Where-Object Result -eq 'PASS').Count
$fail = @($script:Results | Where-Object Result -eq 'FAIL').Count
$script:Results | Format-Table -AutoSize
Write-Host "PASSED: $pass   FAILED: $fail" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
try {
    $script:Results | ConvertTo-Json -Compress | Set-Content "$LogDir\verify_recovery_last.json" -Encoding UTF8
    Write-Host "Saved: logs\verify_recovery_last.json" -ForegroundColor Gray
} catch {}
if ($fail -gt 0) { exit 1 } else { exit 0 }
