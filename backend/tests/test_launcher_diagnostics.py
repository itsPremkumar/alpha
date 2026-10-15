"""Launcher diagnostics: evidence must survive a restart, and health must be served.

Three defects from the 2026-10-01 incident, each pinned against the shipped
``start.ps1`` rather than a description of it:

1. **Crash evidence was destroyed by the recovery.**
   ``Start-Process -RedirectStandardOutput/-RedirectStandardError`` *truncates*
   an existing file on spawn, so every auto-restart wiped the very log that
   would explain the crash - ``logs/gateway.err.log`` never held a reason.
   ``Archive-ServiceLog`` now moves the previous file into
   ``logs\\diagnostics\\`` (timestamped, bounded to the newest 20 per log)
   immediately before every redirecting spawn. The static test fails on any
   spawn site that skips the archive; the behavioural test runs the real
   function.

2. **The boot-wait readiness flags were latches.**
   ``if (-not $gatewayReady)`` probed once and then never again, so a gateway
   that crashed after its first ``/health/ready`` 200 stayed "ready" for the
   rest of the boot wait, and the loop could declare success with a dead
   gateway as soon as the frontend answered once. The real probe block is
   executed here with a sequenced HTTP stub: ready-then-503 must end
   not-ready.

3. **The monitor heartbeat claimed "healthy" from bound ports alone.**
   ``uvicorn`` binds long before ``/health/ready`` answers (migrations + app
   import measured 2-3 min), so the launcher announced healthy while the
   gateway could not serve - the ``status=healthy gateway=starting`` pair the
   watchdog escalated into a full-stack restart loop. ``Write-MonitorHeartbeat``
   must probe both serving routes before writing healthy.

4. **The archive silently failed exactly when it mattered.**
   Observed live on 2026-10-01: ``logs/diagnostics/`` held frontend archives
   but *zero* gateway archives despite multiple gateway spawns. A previous
   incarnation of the service that has not fully exited keeps its
   ``-RedirectStandard*`` file open; ``Move-Item`` then throws
   "being used by another process" and the empty ``catch {}`` swallowed it.
   Two fixes are pinned here: ``Stop-ServiceChainOrphans`` tree-kills the
   port-qualified previous incarnation *before* archiving (a still-booting
   chain is invisible to ``Free-PortOrExit``, which only finds listeners),
   and ``Archive-ServiceLog`` falls back to a copy - the holder allows reads,
   only rename/delete is denied - emitting a visible warning when even that
   fails instead of losing the log in silence.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "start.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="start.ps1 is the Windows PowerShell launcher")

GW_PORT = 8001
FE_PORT = 3000


def _launcher_source() -> str:
    if not LAUNCHER.exists():
        pytest.skip("start.ps1 is not present in this checkout")
    return LAUNCHER.read_text(encoding="utf-8-sig", errors="replace")


def _run(harness_source: str, root: Path, env: dict[str, str]) -> dict[str, str]:
    harness = root / "harness.ps1"
    harness.write_text(harness_source, encoding="utf-8")

    environment = dict(os.environ)
    environment["ALPHA_LS_SRC"] = str(LAUNCHER)
    environment["ALPHA_LS_ROOT"] = str(root)
    environment.update(env)

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        # Generous for the same reason as the watchdog harness: the extracted
        # blocks do no real I/O, so this is PowerShell start-up, and a loaded
        # host makes start-up slow enough to trip a tight bound.
        timeout=300,
        check=False,
    )
    assert "HARNESS_BROKEN" not in proc.stdout, f"start.ps1 was reordered; the extraction harness no longer finds its markers.\n{proc.stdout}\n{proc.stderr}"
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT_"):
            key, _, value = line.partition("=")
            out[key] = value
    assert {"RESULT_STATUS", "RESULT_ORIGINAL_EXISTS", "RESULT_GW", "RESULT_KILLED", "RESULT_LIVENESS", "RESULT_READY", "RESULT_SWEEP"} & set(out), f"harness produced no result\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    return out


# ===========================================================================
# 1. Log archiving before every redirecting spawn
# ===========================================================================

HARNESS_ARCHIVE = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('function Archive-ServiceLog')
$to   = $src.IndexOf('function Get-ListeningProcessIds')
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: markers missing ($from/$to)"
    exit 3
}
$RepoRoot = $env:ALPHA_LS_ROOT
Invoke-Expression ($src.Substring($from, $to - $from))

$target = Join-Path $RepoRoot 'logs\gateway.err.log'
$diag   = Join-Path $RepoRoot 'logs\diagnostics'

if ($env:ALPHA_LS_MODE -eq 'bounded') {
    # 25 pre-existing archives (oldest -> newest), then one more spawn.
    New-Item -ItemType Directory -Path $diag -Force | Out-Null
    for ($i = 1; $i -le 25; $i++) {
        $p = Join-Path $diag ('20260901-{0:D6}.gateway.err.log' -f $i)
        [System.IO.File]::WriteAllText($p, 'old')
        (Get-Item $p).LastWriteTime = (Get-Date).AddMinutes(-$i)
    }
}

# 'held' / 'heldfail' reproduce the live incident: a previous incarnation that
# has not exited keeps the redirect file open. FileShare::Read models the real
# writer (rename/delete denied, reads allowed); FileShare::None denies both the
# move and the copy so the failure path is exercised too.
$fs = $null
if ($env:ALPHA_LS_MODE -eq 'held' -or $env:ALPHA_LS_MODE -eq 'heldfail') {
    [System.IO.File]::WriteAllText($target, 'CRASH: held by a child that never exited')
    $share = if ($env:ALPHA_LS_MODE -eq 'held') { [System.IO.FileShare]::Read } else { [System.IO.FileShare]::None }
    $fs = [System.IO.File]::Open($target, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
}

try {
    $script:ArchResult = Archive-ServiceLog -Path $target -Reason 'test' 3>&1
} finally {
    if ($fs) { $fs.Close() }
}

# 3>&1 merges any Write-Warning records with the function's return string.
$status = @($script:ArchResult | Where-Object { $_ -is [string] }) -join ' '
$warned = [string]::Join(' ', @($script:ArchResult | Where-Object { $_ -isnot [string] } | ForEach-Object { $_.ToString() }))

$archives = @(Get-ChildItem -Path $diag -Filter '*.gateway.err.log' -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
Write-Output ("RESULT_ORIGINAL_EXISTS=" + $(if (Test-Path $target) { 1 } else { 0 }))
Write-Output ("RESULT_ARCHIVE_COUNT=" + $archives.Count)
Write-Output ("RESULT_STATUS=" + $status)
Write-Output ("RESULT_WARN=" + $warned)
$latest = $archives | Select-Object -First 1
if ($latest) {
    $hasCrash = (Get-Content -LiteralPath $latest.FullName -Raw) -like '*CRASH*'
    Write-Output ("RESULT_LATEST_HAS_CRASH=" + $(if ($hasCrash) { 1 } else { 0 }))
} else {
    Write-Output "RESULT_LATEST_HAS_CRASH=0"
}
"""


def test_archive_preserves_the_previous_crash_log(tmp_path: Path) -> None:
    """The truncated-before-it-could-be-read regression: evidence must move, not vanish."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "gateway.err.log").write_text("CRASH: uvicorn exploded", encoding="utf-8")

    out = _run(HARNESS_ARCHIVE, tmp_path, {"ALPHA_LS_MODE": "once"})

    assert out["RESULT_ORIGINAL_EXISTS"] == "0", "the spawn target must be free for Start-Process to create fresh"
    assert out["RESULT_ARCHIVE_COUNT"] == "1"
    assert out["RESULT_LATEST_HAS_CRASH"] == "1", "the archived copy must contain the previous run's bytes"


def test_the_diagnostic_archive_is_bounded(tmp_path: Path) -> None:
    """A restart loop must not fill the disk: keep the newest 20 archives per log."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "gateway.err.log").write_text("CRASH: newest", encoding="utf-8")

    out = _run(HARNESS_ARCHIVE, tmp_path, {"ALPHA_LS_MODE": "bounded"})

    assert out["RESULT_ARCHIVE_COUNT"] == "20"
    assert out["RESULT_LATEST_HAS_CRASH"] == "1", "the newest archive is the one just taken"


def test_an_archive_still_happens_while_the_previous_child_holds_the_log(tmp_path: Path) -> None:
    """The 2026-10-01 finding: zero gateway archives despite repeated spawns.

    A previous incarnation that has not fully exited keeps its redirect file
    open, so ``Move-Item`` throws (rename needs delete access). The copy
    fallback must preserve the bytes anyway - the spawn truncates the live
    file, so the archive is equivalent to a move for diagnostics purposes.
    """
    logs = tmp_path / "logs"
    logs.mkdir()

    out = _run(HARNESS_ARCHIVE, tmp_path, {"ALPHA_LS_MODE": "held"})

    assert out["RESULT_STATUS"] == "copied", "rename is denied by the holder; the function must fall back to a copy"
    assert out["RESULT_ARCHIVE_COUNT"] == "1"
    assert out["RESULT_LATEST_HAS_CRASH"] == "1", "the copy must carry the previous run's crash evidence"
    assert out["RESULT_ORIGINAL_EXISTS"] == "1", "a copy leaves the live file in place for the spawn to truncate"
    assert "Could not archive" not in out["RESULT_WARN"], "a successful copy is not a failure to report"


def test_a_fully_locked_log_fails_loudly_never_silently(tmp_path: Path) -> None:
    """When even the copy is denied, the loss must be visible on the console.

    The pre-fix code swallowed every archive failure in an empty ``catch {}``,
    which is exactly why ``logs/diagnostics/`` never held a gateway log: nobody
    could see that archiving was not happening.
    """
    logs = tmp_path / "logs"
    logs.mkdir()

    out = _run(HARNESS_ARCHIVE, tmp_path, {"ALPHA_LS_MODE": "heldfail"})

    assert out["RESULT_STATUS"] == "failed"
    assert "Could not archive" in out["RESULT_WARN"], f"the failure must surface as a warning, got {out!r}"


def test_every_redirecting_spawn_sweeps_then_archives_first() -> None:
    """Static pairing rule: no Start-Process -FilePath may spawn without sweeping and archiving.

    The window is anchored on the spawn statement itself (not on the redirect
    line) so a missing call cannot be masked by a neighbouring site's call.
    Order inside the window is the order in the file: orphan chains die first
    (they are what keeps the log locked), the archive runs second, the spawn
    truncates last.
    """
    launcher = _launcher_source()

    starts = [m.start() for m in re.finditer(r"Start-Process -FilePath", launcher)]
    assert len(starts) >= 6, f"expected the gateway/frontend spawn sites, found {len(starts)}"
    assert launcher.count("-RedirectStandardOutput") >= 6

    for idx in starts:
        window = launcher[max(0, idx - 400) : idx]
        assert "Stop-ServiceChainOrphans" in window, (
            f"a Start-Process -FilePath at offset {idx} spawns without Stop-ServiceChainOrphans first; a still-booting previous chain holds no listener (Free-PortOrExit misses it) and keeps the redirect log locked"
        )
        assert "Archive-ServiceLog" in window, (
            f"a Start-Process -FilePath at offset {idx} spawns without Archive-ServiceLogs first; Start-Process truncates its -RedirectStandard* targets, destroying the previous run's logs (the crash evidence) the moment a restart begins."
        )
        assert window.index("Stop-ServiceChainOrphans") < window.index("Archive-ServiceLog"), (
            f"a Start-Process -FilePath at offset {idx} archives before sweeping; archiving while the previous child still holds the file is the rename-denied path the sweep exists to avoid."
        )


HARNESS_SWEEP = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('function Archive-ServiceLog')
$to   = $src.IndexOf('function Get-ListeningProcessIds')
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: markers missing ($from/$to)"
    exit 3
}
Invoke-Expression ($src.Substring($from, $to - $from))
if (-not (Get-Command Stop-ServiceChainOrphans -ErrorAction SilentlyContinue)) {
    Write-Output "HARNESS_BROKEN: Stop-ServiceChainOrphans is not defined between Archive-ServiceLog and Get-ListeningProcessIds"
    exit 3
}

$GatewayPort  = 8001
$FrontendPort = 3000
$script:Killed = @()
$fake = @(
    [pscustomobject]@{ ProcessId = 111; CommandLine = 'C:\tools\uv.exe run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001' }
    [pscustomobject]@{ ProcessId = 222; CommandLine = 'C:\repo\backend\.venv\Scripts\uvicorn.exe app.gateway.app:app --host 127.0.0.1 --port 8001' }
    [pscustomobject]@{ ProcessId = 333; CommandLine = 'C:\other\uv.exe run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8999' }
    [pscustomobject]@{ ProcessId = 444; CommandLine = 'C:\node.exe node_modules/next/dist/bin/next dev -p 3000' }
    [pscustomobject]@{ ProcessId = 555; CommandLine = 'C:\node.exe node_modules/next/dist/bin/next start -p 3000' }
    [pscustomobject]@{ ProcessId = 666; CommandLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\alpha\start.ps1' }
    [pscustomobject]@{ ProcessId = 777; CommandLine = $null }
)
$kill = { param($procId) $script:Killed += $procId }

Stop-ServiceChainOrphans -Service 'gateway' -ProcessList $fake -KillAction $kill
$gwKilled = @($script:Killed)
$script:Killed = @()
Stop-ServiceChainOrphans -Service 'frontend' -ProcessList $fake -KillAction $kill
$feKilled = @($script:Killed)

Write-Output ("RESULT_KILLED_GW=" + [string]::Join(',', $gwKilled))
Write-Output ("RESULT_KILLED_FE=" + [string]::Join(',', $feKilled))
Write-Output "RESULT_STATUS=sweep"
"""


def test_orphan_chain_sweep_targets_only_the_port_qualified_incarnation(tmp_path: Path) -> None:
    """Selection must be exact: same service, same port, nothing else.

    - 111/222: the whole gateway chain (uv wrapper and uvicorn child) on 8001
      - both hold inherited redirect handles, both must go.
    - 333: another Alpha instance on 8999 - never touch it.
    - 444/555: frontend ``next dev`` / ``next start`` on 3000 - the frontend
      sweep's job, not the gateway sweep's.
    - 666: the launcher itself; 777: a process with no command line. Both must
      survive a sweep.
    """
    out = _run(HARNESS_SWEEP, tmp_path, {})

    assert out["RESULT_KILLED_GW"] == "111,222", f"gateway sweep must kill both chain links on its port only, got {out!r}"
    assert out["RESULT_KILLED_FE"] == "444,555", f"frontend sweep must kill dev and start forms, got {out!r}"


# ===========================================================================
# 2. Boot-wait readiness must be re-probed, never latched
# ===========================================================================

HARNESS_BOOTWAIT = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('for ($i = 1; $i -le $maxAttempts; $i++) {')
$to   = $src.IndexOf('if (-not $gatewayReady -or -not $frontendReady) {')
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: markers missing ($from/$to)"
    exit 3
}
$loop = $src.Substring($from, $to - $from)

# The state the boot wait starts from.
$GatewayPort  = 8001
$FrontendPort = 3000
$maxAttempts       = [int]$env:ALPHA_LS_MAX_ATTEMPTS
$gatewayReady      = $false
$frontendReady     = $false
$gatewayOkLogged   = $false
$frontendOkLogged  = $false
$gatewayProcess    = $null
$frontendProcess   = $null
$script:LastGatewayProbeError  = ''
$script:HealthWrites           = @()
$script:UnexpectedRestarts     = 0

# Sequenced HTTP answers per URI: values are consumed one per probe, the last
# one repeating. "ERR" makes Invoke-WebRequest throw a connection failure.
$gwRaw = $env:ALPHA_LS_GW
$feRaw = $env:ALPHA_LS_FE
$script:GwIdx = 0
$script:FeIdx = 0
function Next-Code {
    param([string]$Key, [string]$Raw)
    $list = $Raw -split ','
    $i = if ($Key -eq 'gw') { $script:GwIdx } else { $script:FeIdx }
    if ($i -ge $list.Count) { $i = $list.Count - 1 }
    if ($Key -eq 'gw') { $script:GwIdx = $i + 1 } else { $script:FeIdx = $i + 1 }
    return $list[$i]
}
function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec = 5)
    $code = if ($Uri -like '*/health/ready') { Next-Code -Key 'gw' -Raw $gwRaw } else { Next-Code -Key 'fe' -Raw $feRaw }
    if ($code -eq 'ERR') { throw 'Unable to connect to the remote server' }
    if ($code -ne '200') { throw "($code) Service Unavailable" }
    return [pscustomobject]@{ StatusCode = 200; Content = '' }
}

# Everything else the loop body touches.
function Test-MaintenanceMode { return $false }
function Stop-OnMaintenance {}
function Start-Sleep { param([int]$Seconds) }
function Write-HealthFile {
    param([string]$Status = 'healthy', [string]$Detail = '', [hashtable]$Extra = $null)
    $script:HealthWrites += "$Status|$Detail"
}
function Update-TrackedProcess { param($Process, $Port) return $Process }
function Test-PortListening { param([int]$Port) return $true }
function Restart-GatewayService { param([int]$Attempt) $script:UnexpectedRestarts += 1 }
function Restart-FrontendService { param([int]$Attempt) $script:UnexpectedRestarts += 1 }
function Show-LogTail { param($Path) }
function Reset-GatewayBackoffIfStable {}
function Reset-FrontendBackoffIfStable {}

Invoke-Expression $loop

Write-Output ("RESULT_GW=" + $(if ($gatewayReady) { 'true' } else { 'false' }))
Write-Output ("RESULT_FE=" + $(if ($frontendReady) { 'true' } else { 'false' }))
Write-Output ("RESULT_I=" + $i)
Write-Output ("RESULT_UNEXPECTED_RESTARTS=" + $script:UnexpectedRestarts)
Write-Output ("RESULT_GW_ERROR=" + $script:LastGatewayProbeError)
Write-Output ("RESULT_HEALTH_WRITES=" + [string]::Join(' || ', $script:HealthWrites))
"""


def _bootwait(tmp_path: Path, *, gw: str, fe: str, attempts: int) -> dict[str, str]:
    return _run(
        HARNESS_BOOTWAIT,
        tmp_path,
        {
            "ALPHA_LS_GW": gw,
            "ALPHA_LS_FE": fe,
            "ALPHA_LS_MAX_ATTEMPTS": str(attempts),
        },
    )


def test_gateway_readiness_is_not_latched_true(tmp_path: Path) -> None:
    """200 then a crash: the boot wait must flip back to not-ready.

    On the pre-fix code the second pass was skipped by the
    ``if (-not $gatewayReady)`` guard, so a gateway that died after its first
    success stayed "ready" and the loop could break out with it dead.
    """
    out = _bootwait(tmp_path, gw="200,503", fe="503", attempts=2)

    assert out["RESULT_GW"] == "false", "readiness must be re-probed every pass, not latched"
    assert out["RESULT_FE"] == "false"
    assert out["RESULT_UNEXPECTED_RESTARTS"] == "0"


def test_frontend_readiness_is_not_latched_true(tmp_path: Path) -> None:
    """The mirror image: a frontend that dies after one 200 must flip false.

    This run also proves the gateway flag recovers false -> true when the
    service comes back, i.e. the probe is a fresh fact in both directions.
    """
    out = _bootwait(tmp_path, gw="503,200", fe="200,503", attempts=2)

    assert out["RESULT_FE"] == "false", "the frontend probe must not be skipped after its first 200"
    assert out["RESULT_GW"] == "true", "a gateway that starts serving late must become ready"
    assert out["RESULT_UNEXPECTED_RESTARTS"] == "0"


def test_the_boot_wait_still_breaks_out_on_success(tmp_path: Path) -> None:
    """Re-probing must not break the happy path: both serving on pass 1 -> break."""
    out = _bootwait(tmp_path, gw="200", fe="200", attempts=3)

    assert out["RESULT_GW"] == "true"
    assert out["RESULT_FE"] == "true"
    assert out["RESULT_I"] == "1", "the loop must break at pass 1, not run to maxAttempts"
    assert out["RESULT_HEALTH_WRITES"], "the boot-wait record must still be written every pass"


def test_boot_wait_probes_are_not_latched_in_source() -> None:
    """The latch guards themselves must be gone, so a re-add fails loudly."""
    launcher = _launcher_source()
    assert "if (-not $gatewayReady) {" not in launcher, "the gateway boot probe is guarded by its own previous result; readiness must be re-probed every pass"
    assert "if (-not $frontendReady) {" not in launcher, "the frontend boot probe is guarded by its own previous result; readiness must be re-probed every pass"


HARNESS_WAIT_FOR_HEALTHY = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('function Wait-ForHealthy')
$to   = $src.IndexOf('function Start-SleepWithHeartbeat', $from)
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: Wait-ForHealthy markers missing"
    exit 3
}
Invoke-Expression ($src.Substring($from, $to - $from))
$script:ProbeCalls = 0
function Test-MaintenanceMode { return $false }
function Stop-OnMaintenance {}
function Start-Sleep { param([int]$Seconds) }
function Refresh-Heartbeat {}
function Get-ListeningProcessIds { param([int]$Port) return @() }
function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec = 5)
    $script:ProbeCalls += 1
    if ($script:ProbeCalls -eq 1) { throw 'Connection refused while the app imports' }
    return [pscustomobject]@{ StatusCode = 200; Content = '' }
}
$result = Wait-ForHealthy -Port 8001 -Path '/health/ready' -MaxWaitSeconds 20
Write-Output ("RESULT_READY=" + [string]$result)
Write-Output ("RESULT_PROBES=" + $script:ProbeCalls)
"""


def test_gateway_restart_waits_through_import_before_listener_binds(tmp_path: Path) -> None:
    """A closed port during app import is still booting, not a failed launch."""
    out = _run(HARNESS_WAIT_FOR_HEALTHY, tmp_path, {})

    assert out["RESULT_READY"] == "True", "Wait-ForHealthy must retry readiness while the import-time port is still closed"
    assert out["RESULT_PROBES"] == "2", "the second ready probe must be reached after an initial connection refusal"


# ===========================================================================
# 3. The monitor heartbeat must probe serving, not just bound ports
# ===========================================================================

HARNESS_MONITOR = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('function Test-Serving')
$to   = $src.IndexOf('function Wait-ForHealthy')
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: markers missing ($from/$to)"
    exit 3
}
Invoke-Expression ($src.Substring($from, $to - $from))

$GatewayPort  = 8001
$FrontendPort = 3000
$ports  = $env:ALPHA_LS_PORTS -split ','
$gwCode = $env:ALPHA_LS_MON_GW
$feCode = $env:ALPHA_LS_MON_FE
$script:LastStatus = ''
$script:LastDetail = ''

function Test-PortListening { param([int]$Port) return ($ports -contains "$Port") }
function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec = 5)
    $code = if ($Uri -like '*:3000/*') { $feCode } else { $gwCode }
    if ($code -ne '200') { throw "($code) Service Unavailable" }
    return [pscustomobject]@{ StatusCode = 200; Content = '' }
}
function Write-HealthFile {
    param([string]$Status = 'healthy', [string]$Detail = '', [hashtable]$Extra = $null)
    $script:LastStatus = $Status
    $script:LastDetail = $Detail
}

Write-MonitorHeartbeat
Write-Output ("RESULT_STATUS=" + $script:LastStatus)
Write-Output ("RESULT_DETAIL=" + $script:LastDetail)
"""


def _monitor(tmp_path: Path, *, ports: str, gw: str, fe: str) -> dict[str, str]:
    return _run(
        HARNESS_MONITOR,
        tmp_path,
        {
            "ALPHA_LS_PORTS": ports,
            "ALPHA_LS_MON_GW": gw,
            "ALPHA_LS_MON_FE": fe,
        },
    )


def test_monitor_heartbeat_claims_healthy_only_when_both_serve(tmp_path: Path) -> None:
    """Ports bound AND both routes answering 200 - the only honest 'healthy'."""
    out = _monitor(tmp_path, ports=f"{GW_PORT},{FE_PORT}", gw="200", fe="200")
    assert out["RESULT_STATUS"] == "healthy"


def test_a_bound_gateway_that_cannot_serve_is_not_healthy(tmp_path: Path) -> None:
    """The incident's exact lie: bound port, /health/ready answering 503.

    The old heartbeat looked at ports only, wrote "healthy", and the watchdog
    escalated the mismatch into a full-stack restart.
    """
    out = _monitor(tmp_path, ports=f"{GW_PORT},{FE_PORT}", gw="503", fe="200")
    assert out["RESULT_STATUS"] != "healthy", "a gateway whose /health/ready fails must never be announced healthy"
    assert "gateway=False" in out["RESULT_DETAIL"], out


def test_a_down_port_still_reports_degraded(tmp_path: Path) -> None:
    """The port-down case keeps its existing label and detail."""
    out = _monitor(tmp_path, ports=str(FE_PORT), gw="200", fe="200")
    assert out["RESULT_STATUS"] == "degraded"
    assert "waiting for gateway/frontend to return" in out["RESULT_DETAIL"]


def test_the_monitor_loop_actually_calls_the_serving_heartbeat() -> None:
    """The function must be wired into the loop, not exist as dead code."""
    launcher = _launcher_source()
    assert launcher.count("Write-MonitorHeartbeat") >= 2, "Write-MonitorHeartbeat must be defined and called from the monitor loop; a heartbeat that only checks ports reintroduces the healthy-while-starting lie"


HARNESS_SERVICE_LIVENESS = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('function Test-ServiceListenerMissing')
$to = $src.IndexOf('# -- Terminal-state bookkeeping', $from)
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: liveness helper markers missing ($from/$to)"
    exit 3
}
Invoke-Expression ($src.Substring($from, $to - $from))
function Test-PortListening { param([int]$Port) return ($env:ALPHA_LS_PORT_UP -eq "$Port") }
$result = Test-ServiceListenerMissing -Port 8001
Write-Output ("RESULT_LIVENESS=" + [string]$result)
"""


@pytest.mark.parametrize(("port_up", "expected"), [("", "True"), ("8001", "False")])
def test_service_liveness_uses_the_listener_even_if_process_survives(tmp_path: Path, port_up: str, expected: str) -> None:
    """A live process cannot make a service healthy when its port is closed."""
    out = _run(HARNESS_SERVICE_LIVENESS, tmp_path, {"ALPHA_LS_PORT_UP": port_up})
    assert out["RESULT_LIVENESS"] == expected


def test_steady_state_monitor_restarts_a_live_process_with_no_listener() -> None:
    """The restart decision must not wait for a surviving wrapper PID to exit."""
    launcher = _launcher_source()
    monitor = launcher[launcher.index("# -- 9. Keep Running and Monitor") :]
    gateway_match = re.search(r"\$gatewayGone\s*=\s*(.+)", monitor)
    frontend_match = re.search(r"\$frontendGone\s*=\s*(.+)", monitor)
    assert gateway_match and "Test-ServiceListenerMissing -Port $GatewayPort" in gateway_match.group(1)
    assert frontend_match and "Test-ServiceListenerMissing -Port $FrontendPort" in frontend_match.group(1)
    assert ".HasExited" not in gateway_match.group(1)
    assert ".HasExited" not in frontend_match.group(1)


def test_service_liveness_helper_checks_the_port() -> None:
    """The helper used by the steady-state monitor must check listener state."""
    launcher = _launcher_source()
    start = launcher.index("function Test-ServiceListenerMissing")
    end = launcher.index("# -- Terminal-state bookkeeping", start)
    helper = launcher[start:end]
    assert "return -not (Test-PortListening -Port $Port)" in helper


# ===========================================================================
# 5. A gateway that hangs during import holds no port - and no port-based
#    killer can ever see it
# ===========================================================================
#
# `uvicorn` binds 8001 only AFTER the application import completes (2-3 min,
# measured). A wedged import - a deadlock in module-level code, a blocking
# network call - therefore produces a process that is alive, holds NO listener,
# and emits nothing useful. That state is invisible to every port-based killer:
# the watchdog's Stop-PortTree only finds LISTENING pids, so its Tier-2 restart
# is a permanent no-op against it, and only a command-line match finds it.
#
# Before this was handled, the boot wait simply ran out its full 15-minute budget
# and then FAILED the entire startup (exit 1). A hung import during a
# watchdog-spawned boot is recoverable - kill it by command line and try again -
# but nothing ever did that.

HARNESS_HUNG_GATEWAY = r"""
$src = Get-Content -LiteralPath $env:ALPHA_LS_SRC -Raw
$from = $src.IndexOf('for ($i = 1; $i -le $maxAttempts; $i++) {')
$to   = $src.IndexOf('if (-not $gatewayReady -or -not $frontendReady) {')
if ($from -lt 0 -or $to -le $from) {
    Write-Output "HARNESS_BROKEN: markers missing ($from/$to)"
    exit 3
}
$loop = $src.Substring($from, $to - $from)

# Read the shipped threshold rather than hard-coding it: a rename or a removal
# must fail the harness loudly instead of silently exercising a branch whose
# constant does not exist (PowerShell reads an undefined name as $null, and
# `$null -gt 45` is simply false - a test that could only ever pass).
$m = [regex]::Match($src, '(?m)^\s*\$GatewayNoListenerSeconds\s*=\s*(\d+)')
if (-not $m.Success) {
    Write-Output 'HARNESS_BROKEN: $GatewayNoListenerSeconds is not a literal int in start.ps1'
    exit 3
}
$GatewayNoListenerSeconds = [int]$m.Groups[1].Value

$GatewayPort  = 8001
$FrontendPort = 3000
$maxAttempts       = [int]$env:ALPHA_LS_MAX_ATTEMPTS
$gatewayReady      = $false
$frontendReady     = $false
$gatewayOkLogged   = $false
$frontendOkLogged  = $false
# Alive (the wrapper never exits) but holding no port: the hung-import state.
$gatewayProcess    = [pscustomobject]@{ HasExited = $false; ExitCode = $null; Id = 4242 }
$frontendProcess   = [pscustomobject]@{ HasExited = $false; ExitCode = $null; Id = 4243 }
$script:LastGatewayProbeError  = ''
$script:HealthWrites           = @()
$script:Swept                  = @()
$script:Restarts               = @()
$script:GatewayPortUp          = ($env:ALPHA_LS_GW_PORT -eq '1')
# How long the current gateway incarnation has been up. A hang is measured from
# the spawn, so the test back-dates it rather than sleeping through the budget.
$script:GatewaySpawnedUtc      = [DateTime]::UtcNow.AddSeconds(-([double]$env:ALPHA_LS_GW_AGE))

# Nothing is serving: every readiness probe fails, exactly as during a hang.
function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec = 5)
    throw 'nothing is listening'
}
function Test-MaintenanceMode { return $false }
function Stop-OnMaintenance {}
function Start-Sleep { param([int]$Seconds) }
function Write-HealthFile { param([string]$Status = 'healthy', [string]$Detail = '', [hashtable]$Extra = $null) $script:HealthWrites += $Status }
function Update-TrackedProcess { param($Process, $Port) return $Process }
function Test-PortListening { param([int]$Port) return (($Port -eq $GatewayPort) -and $script:GatewayPortUp) }
function Restart-GatewayService { param([int]$Attempt) $script:Restarts += $Attempt }
function Restart-FrontendService { param([int]$Attempt) }
# The only primitive that finds a process holding no port.
function Stop-ServiceChainOrphans {
    param([string]$Service, $ProcessList = $null, $KillAction = $null)
    $script:Swept += $Service
}
function Show-LogTail { param($Path) }
function Reset-GatewayBackoffIfStable {}
function Reset-FrontendBackoffIfStable {}

Invoke-Expression $loop

Write-Output ("RESULT_SWEEP=" + [string]::Join(',', $script:Swept))
Write-Output ("RESULT_RESTARTS=" + [string]::Join(',', $script:Restarts))
Write-Output ("RESULT_I=" + $i)
Write-Output ("RESULT_HEALTH=" + [string]::Join(',', $script:HealthWrites))
"""


def _hung_gateway(tmp_path: Path, *, age_seconds: int, attempts: int) -> dict[str, str]:
    return _run(
        HARNESS_HUNG_GATEWAY,
        tmp_path,
        {
            "ALPHA_LS_GW_AGE": str(age_seconds),
            "ALPHA_LS_GW_PORT": "0",
            "ALPHA_LS_MAX_ATTEMPTS": str(attempts),
        },
    )


def test_a_gateway_that_never_binds_its_port_is_killed_and_respawned(tmp_path: Path) -> None:
    """The hang must cost one restart, not the whole 15-minute boot budget.

    ``$gatewayGone`` cannot see this state - the wrapper process is still alive -
    so the loop has to reach it by its own rule: no listener since the spawn,
    past a budget the launcher already considers a failed boot.
    """
    out = _hung_gateway(tmp_path, age_seconds=10_000, attempts=1)

    assert out["RESULT_SWEEP"] == "gateway", f"the hung chain holds no port, so only a command-line match can find it; got {out!r}"
    assert out["RESULT_RESTARTS"] != "", "killing it is only useful if a replacement is spawned"


def test_a_slow_but_normal_gateway_import_is_not_killed(tmp_path: Path) -> None:
    """Import takes 2-3 min; the rule must not fire inside that.

    A launcher that kills every gateway still importing has traded a hang for a
    restart loop, which is the same failure with better log messages.
    """
    out = _hung_gateway(tmp_path, age_seconds=45, attempts=3)

    assert out["RESULT_SWEEP"] == "", f"a gateway still inside its import budget must be left alone, got {out!r}"
    assert out["RESULT_RESTARTS"] == ""


def test_the_hung_gateway_rule_needs_a_visible_listener_to_not_fire(tmp_path: Path) -> None:
    """A bound gateway is past the import; the rule must switch off.

    Old age plus a working port means the gateway came up and the port went away
    again. That is ``$gatewayGone``'s business if the process died, and the
    launcher's own restart path if it did not - not this rule's.
    """
    root = tmp_path / "bound"
    root.mkdir()
    out = _run(
        HARNESS_HUNG_GATEWAY,
        root,
        {
            "ALPHA_LS_GW_AGE": "10000",
            "ALPHA_LS_GW_PORT": "1",
            "ALPHA_LS_MAX_ATTEMPTS": "2",
        },
    )

    assert out["RESULT_SWEEP"] == "", f"a bound gateway is not a hung import, got {out!r}"


def test_the_hung_gateway_rule_is_wired_into_the_boot_wait() -> None:
    """The branch must live in the shipped boot wait, not in a helper nobody calls."""
    launcher = _launcher_source()
    loop_start = launcher.index("for ($i = 1; $i -le $maxAttempts; $i++) {")
    loop_end = launcher.index("if (-not $gatewayReady -or -not $frontendReady) {")
    boot_wait = launcher[loop_start:loop_end]
    assert "Stop-ServiceChainOrphans" in boot_wait, "the boot wait must reach the hung-import case by command-line match; a port-based check can never observe a process that is still importing"
    assert "GatewaySpawnedUtc" in boot_wait, "the hang must be measured from the spawn, not from the process start time"
    assert launcher.count("GatewaySpawnedUtc = [DateTime]::UtcNow") >= 2, "every gateway spawn site must reset the clock, or a respawned gateway inherits the previous incarnation's age and is killed the instant it boots"
