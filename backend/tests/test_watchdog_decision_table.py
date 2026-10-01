"""The watchdog's escalation table, executed as shipped - not described.

The measured incident (2026-10-01): the gateway needs ~2-3 minutes to answer
``/health/ready``, so ``Get-StackSnapshot`` reported
``gateway=starting ... launcher=alive launcher_hb=fresh status=healthy`` for 28
consecutive checks, and the next pass reached the Tier-3 branch and logged::

    stack :: full_restart -> spawned (escalation after 28 failing checks:
    gateway=starting frontend=up launcher=alive launcher_hb=fresh status=healthy)

which destroyed a stack that was mid-boot and became the restart loop the tray
was stuck in. The decision table has three layers - defer -> component restart
-> full stack - and the full-stack step is now gated: it may run only when the
launcher cannot act (dead or heartbeat-stale) or when a component has
exhausted its Tier-2 budget. A deferral must also not feed the escalation
counter, and the cooldown branch must actually ``return`` (PowerShell parses a
bare ``return`` trailing another statement on the same line as an *argument*,
so the merged form silently fell through into Tier 2/3).

These tests extract the real config block, ``Get-StackSnapshot`` and
``Invoke-HealthCheck`` out of ``recovery/watchdog.ps1`` under PowerShell, with
every probe, every lock primitive and every destructive action replaced by a
controllable stub. They exercise the shipped decision table and fail on the
pre-fix behaviour: an unguarded full-stack restart at pass ``n+1``, a cooldown
pass that acts twice, and a component-level state that gets the whole tree
killed. Keep the extraction markers stable; if ``watchdog.ps1`` is reordered
the harness fails loudly instead of silently reporting a wrong decision.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "recovery" / "watchdog.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="recovery/watchdog.ps1 is the Windows PowerShell supervisor")

GW_PORT = 8001
FE_PORT = 3000
BOTH_PORTS = f"{GW_PORT},{FE_PORT}"

# PowerShell that loads the real decision table and prints what it decided.
#
# Extraction is positional on purpose: config globals (budgets + counters),
# then Get-StackSnapshot (probe -> state classification), then
# Invoke-HealthCheck (the decision). Everything else the two functions call
# (locks, logging, the health file, the destructive actions, the port/HTTP
# probes themselves, and Get-Process for launcher uptime) is stubbed below, so
# a scenario is expressed purely as observable inputs.
HARNESS = r"""
$src = Get-Content -LiteralPath $env:ALPHA_WD_SRC -Raw

$cfgFrom = $src.IndexOf('$ErrorActionPreference = "SilentlyContinue"')
$cfgTo   = $src.IndexOf('if (-not (Test-Path $LogDir))')
$stkFrom = $src.IndexOf('function Get-StackSnapshot')
$hpFrom  = $src.IndexOf('function Invoke-HealthCheck')
$l4From  = $src.IndexOf('function Invoke-WatchdogOfWatchdog')
if ($cfgFrom -lt 0 -or $cfgTo -le $cfgFrom -or $stkFrom -lt 0 -or $hpFrom -le $stkFrom -or $l4From -le $hpFrom) {
    Write-Output "HARNESS_BROKEN: markers missing (cfg=$cfgFrom/$cfgTo stk=$stkFrom hp=$hpFrom l4=$l4From)"
    exit 3
}
Invoke-Expression ($src.Substring($cfgFrom, $cfgTo - $cfgFrom))
Invoke-Expression ($src.Substring($stkFrom, $hpFrom - $stkFrom))
Invoke-Expression ($src.Substring($hpFrom, $l4From - $hpFrom))
$ErrorActionPreference = 'Continue'

# ---- controllable probes --------------------------------------------------
$ports      = $env:ALPHA_WD_PORTS -split ','
$https      = $env:ALPHA_WD_HTTP_OK -split ','
$logAdv     = ($env:ALPHA_WD_LOG_ADVANCING -eq '1')
$launcherUp = ($env:ALPHA_WD_LAUNCHER -eq '1')
$hbFresh    = ($env:ALPHA_WD_HB -eq '1')
$wdStatus   = $env:ALPHA_WD_STATUS
$lockFree   = ($env:ALPHA_WD_LOCK_HELD -ne '0')

function Test-PortListening { param([int]$Port) return ($ports -contains "$Port") }
function Test-HttpOk { param([int]$Port, [string]$Path) return ($https -contains "$Port") }
function Test-FileRecentlyWritten { param([string]$Path, [int]$WithinSeconds = 120) return $logAdv }
function Get-HealthState { return @{ status = $wdStatus } }
function Test-LauncherAlive { return $launcherUp }
function Test-LauncherHeartbeatFresh { param($Health) return $hbFresh }
function Get-PidFileValue { param([string]$Path) return $PID }
function Get-Process {
    [CmdletBinding()]
    param([Parameter(Position = 0)]$Id)
    $up = 120.0
    if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_LAUNCHER_UPTIME)) { $up = [double]$env:ALPHA_WD_LAUNCHER_UPTIME }
    return [pscustomobject]@{ StartTime = [DateTime]::Now.AddSeconds(-$up) }
}

# ---- recording stubs ------------------------------------------------------
$script:ActionLog    = @()
$script:Heartbeats   = @()
$script:LogLines     = @()
$script:LockTaken    = 0
$script:LockReleased = 0

function Write-Heartbeat {
    param([string]$Status, [string]$Stack = "", [Parameter(ValueFromRemainingArguments)]$Rest)
    $script:Heartbeats += $Status
}
function Write-WdLog {
    param([string]$Message, [string]$Level = "INFO")
    $script:LogLines += "[$Level] $Message"
}
function Enter-SupervisorLock {
    if (-not $lockFree) { return $false }
    $script:LockTaken += 1
    return $true
}
function Exit-SupervisorLock { $script:LockReleased += 1 }
function Restart-Component { param([string]$Component) $script:ActionLog += "component:$Component" }
function Start-AlphaStack { param([string]$Reason) $script:ActionLog += "stack"; return $true }

# ---- optional seeding -----------------------------------------------------
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_N))        { $script:ConsecutiveFailures = [int]$env:ALPHA_WD_SEED_N }
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_DEFER))    { $script:DeferPasses = [int]$env:ALPHA_WD_SEED_DEFER }
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_ATTEMPTS)) { $script:ComponentAttempts = @{ gateway = [int]$env:ALPHA_WD_SEED_ATTEMPTS } }
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_BACKOFF)) {
    $script:ActionBackoff = [int]$env:ALPHA_WD_SEED_BACKOFF
    $script:LastActionUtc = [DateTime]::UtcNow
}
if ($env:ALPHA_WD_SEED_MAINT -eq '1') { $script:MaintenanceLogged = $true }

# ---- driver ---------------------------------------------------------------
$passes = 1
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_PASSES)) { $passes = [int]$env:ALPHA_WD_PASSES }
$clearCooldown = ($env:ALPHA_WD_CLEAR_COOLDOWN -eq '1')
for ($p = 1; $p -le $passes; $p++) {
    if ($clearCooldown) { $script:LastActionUtc = $null; $script:ActionBackoff = 0 }
    Invoke-HealthCheck
}

$attempts = 0
if ($script:ComponentAttempts.Count -gt 0) {
    $m = ($script:ComponentAttempts.Values | Measure-Object -Maximum).Maximum
    if ($null -ne $m) { $attempts = [int]$m }
}
Write-Output ("RESULT_HEARTBEATS=" + [string]::Join(',', $script:Heartbeats))
Write-Output ("RESULT_ACTIONS=" + [string]::Join(',', $script:ActionLog))
Write-Output ("RESULT_LOG=" + [string]::Join(' | ', $script:LogLines))
Write-Output ("RESULT_CONSECUTIVE=" + $script:ConsecutiveFailures)
Write-Output ("RESULT_DEFER=" + $script:DeferPasses)
Write-Output ("RESULT_ATTEMPTS=" + $attempts)
Write-Output ("RESULT_BACKOFF=" + $script:ActionBackoff)
Write-Output ("RESULT_LOCK_TAKEN=" + $script:LockTaken)
Write-Output ("RESULT_LOCK_RELEASED=" + $script:LockReleased)
Write-Output ("RESULT_MAINT=" + $(if ($script:MaintenanceLogged) { 1 } else { 0 }))
"""


def _ps_int(source: str, name: str) -> int:
    match = re.search(rf"^\s*\${re.escape(name)}\s*=\s*(\d+)\s*(?:#.*)?$", source, re.MULTILINE)
    assert match, f"could not find ${name} = <int> - the watchdog config changed shape"
    return int(match.group(1))


def _state(
    *,
    ports: str = BOTH_PORTS,
    http_ok: str = BOTH_PORTS,
    status: str = "healthy",
    launcher: int = 1,
    hb: int = 1,
    uptime: int = 2400,
    log_advancing: bool = False,
    lock_available: bool = True,
    **extra: str,
) -> dict[str, str]:
    """One scenario's observable world: who listens, who serves, who claims what."""
    env = {
        "ALPHA_WD_PORTS": ports,
        "ALPHA_WD_HTTP_OK": http_ok,
        "ALPHA_WD_STATUS": status,
        "ALPHA_WD_LAUNCHER": str(launcher),
        "ALPHA_WD_HB": str(hb),
        "ALPHA_WD_LAUNCHER_UPTIME": str(uptime),
        "ALPHA_WD_LOG_ADVANCING": "1" if log_advancing else "0",
        "ALPHA_WD_LOCK_HELD": "1" if lock_available else "0",
    }
    env.update(extra)
    return env


def _run(tmp_path: Path, env: dict[str, str]) -> dict[str, str]:
    """Execute the real Get-StackSnapshot + Invoke-HealthCheck, return their record."""
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")

    environment = dict(os.environ)
    environment["ALPHA_WD_SRC"] = str(WATCHDOG)
    environment.update(env)

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=120,
        check=False,
    )
    assert "HARNESS_BROKEN" not in proc.stdout, f"recovery/watchdog.ps1 was reordered; the extraction harness no longer finds its markers.\n{proc.stdout}\n{proc.stderr}"
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT_"):
            key, _, value = line.partition("=")
            out[key] = value
    assert "RESULT_HEARTBEATS" in out, f"harness produced no decision\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    return out


def _actions(out: dict[str, str]) -> list[str]:
    return [a for a in out["RESULT_ACTIONS"].split(",") if a]


def _heartbeats(out: dict[str, str]) -> list[str]:
    return [h for h in out["RESULT_HEARTBEATS"].split(",") if h]


def _budget(watchdog: str, name: str) -> int:
    return _ps_int(watchdog, name)


def _old_launcher(watchdog: str) -> int:
    """An uptime past the 3x working-defer cap: Tier 1 must stop deferring."""
    return _budget(watchdog, "StartupGraceSeconds") * 3 + 600


YOUNG_LAUNCHER = 120


def test_a_healthy_stack_is_ok_and_resets_every_counter(tmp_path: Path, watchdog: str) -> None:
    """The happy path must clear ALL stale state, or a later pass acts on memory."""
    out = _run(
        tmp_path,
        _state(
            ALPHA_WD_PASSES="1",
            ALPHA_WD_SEED_N="5",
            ALPHA_WD_SEED_DEFER="7",
            ALPHA_WD_SEED_ATTEMPTS="99",
            ALPHA_WD_SEED_BACKOFF="100",
            ALPHA_WD_SEED_MAINT="1",
        ),
    )
    assert out["RESULT_HEARTBEATS"] == "ok"
    assert _actions(out) == []
    assert out["RESULT_CONSECUTIVE"] == "0"
    assert out["RESULT_DEFER"] == "0"
    assert out["RESULT_ATTEMPTS"] == "0"
    assert out["RESULT_BACKOFF"] == "0"
    assert out["RESULT_MAINT"] == "0"


def test_working_launcher_defer_does_not_feed_the_escalation_counter(tmp_path: Path, watchdog: str) -> None:
    """Tier 1 deferral must reset n, not increment it.

    Feeding the counter is what let a long boot accumulate 28 failures, so the
    first pass after a working period escalated instantly. ``DeferPasses`` is
    the logging-only counter those passes count against instead.
    """
    out = _run(
        tmp_path,
        _state(
            ports=BOTH_PORTS,
            http_ok=str(FE_PORT),  # gateway listening but not yet serving
            status="starting",
            uptime=YOUNG_LAUNCHER,
            ALPHA_WD_PASSES="3",
        ),
    )
    assert _heartbeats(out) == ["deferring", "deferring", "deferring"]
    assert _actions(out) == []
    assert out["RESULT_CONSECUTIVE"] == "0", "a deferral must not feed ConsecutiveFailures"
    assert out["RESULT_DEFER"] == "3"


def test_a_healthy_claim_with_a_starting_gateway_is_always_deferred(tmp_path: Path, watchdog: str) -> None:
    """The measured incident, replayed: it must defer forever, never restart.

    The launcher claims ``status=healthy`` while the gateway port is bound but
    ``/health/ready`` has not answered yet - exactly the state that produced
    "escalation after 28 failing checks" and the full-restart loop. The
    healthy-claim budget (n <= $MaxDeferredRecoveries) defers first; when it
    runs out, the Tier-3 gate must defer again (and release the supervisor
    lock it took to decide) instead of killing the stack.
    """
    passes = _budget(watchdog, "MaxDeferredRecoveries") + 2
    out = _run(
        tmp_path,
        _state(
            http_ok=str(FE_PORT),
            status="healthy",
            uptime=YOUNG_LAUNCHER,
            ALPHA_WD_PASSES=str(passes),
        ),
    )
    assert _heartbeats(out) == ["deferring"] * passes, out
    assert _actions(out) == [], "a live launcher mid-boot must never receive a full-stack restart"
    assert int(out["RESULT_DEFER"]) == passes
    # The gate path takes the supervisor lock to make its decision and must
    # give it back, or a later pass that legitimately needs to act is blocked.
    assert out["RESULT_LOCK_TAKEN"] == "1"
    assert out["RESULT_LOCK_RELEASED"] == "1"


def test_a_persistently_unhealthy_gateway_gets_a_component_restart_not_a_stack_kill(tmp_path: Path, watchdog: str) -> None:
    """Beyond every defer budget, escalation must stop at Tier 2 while the launcher lives.

    The gateway never answers, so it walks ``starting -> hung`` at
    $GatewayHungThreshold. With the launcher alive and heartbeating, the Tier-3
    gate still refuses to destroy the whole tree: only the bad component is
    restarted, once, and no ``stack`` action ever appears no matter how many
    more checks pass.
    """
    passes = _budget(watchdog, "MaxDeferredRecoveries") + _budget(watchdog, "GatewayHungThreshold") + 5
    out = _run(
        tmp_path,
        _state(
            http_ok=str(FE_PORT),
            status="healthy",
            uptime=YOUNG_LAUNCHER,
            ALPHA_WD_PASSES=str(passes),
        ),
    )
    assert _actions(out) == ["component:gateway"], out
    assert _heartbeats(out).count("recovering") == 1
    assert "stack" not in _actions(out), "the full stack must not be killed while the launcher can act"


def test_a_dead_gateway_is_restarted_as_a_component_first(tmp_path: Path, watchdog: str) -> None:
    """Tier 2 is the smallest safe recovery; it must run before any escalation."""
    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),  # gateway port closed entirely
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="1",
            ALPHA_WD_SEED_DEFER="7",
        ),
    )
    assert _actions(out) == ["component:gateway"]
    assert _heartbeats(out) == ["recovering"]
    assert out["RESULT_CONSECUTIVE"] == "0"
    assert out["RESULT_ATTEMPTS"] == "1"
    assert int(out["RESULT_BACKOFF"]) > 0, "an action must arm the cooldown"
    assert out["RESULT_DEFER"] == "0", "an action ends the deferral streak"


def test_exhausted_component_budget_escalates_to_the_stack(tmp_path: Path, watchdog: str) -> None:
    """The gate's other permitted case: Tier 2 budget exhausted -> full restart."""
    max_components = _budget(watchdog, "MaxComponentRecoveries")
    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES=str(max_components + 1),
            ALPHA_WD_CLEAR_COOLDOWN="1",
        ),
    )
    assert _actions(out) == ["component:gateway"] * max_components + ["stack"], out
    assert f"exceeded {max_components} component restarts" in out["RESULT_LOG"]
    assert out["RESULT_CONSECUTIVE"] == "0"
    assert out["RESULT_ATTEMPTS"] == "0"
    assert out["RESULT_DEFER"] == "0"


@pytest.mark.parametrize(
    ("launcher", "hb"),
    [
        (0, 0),  # launcher process gone
        (1, 0),  # launcher alive but its heartbeat is stale (frozen)
    ],
    ids=["dead", "frozen"],
)
def test_a_launcher_that_cannot_act_is_replaced(tmp_path: Path, watchdog: str, launcher: int, hb: int) -> None:
    """The gate's first permitted case: nobody else can fix it, so the stack is restarted."""
    out = _run(
        tmp_path,
        _state(
            status="starting",
            launcher=launcher,
            hb=hb,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="1",
        ),
    )
    assert _actions(out) == ["stack"]
    assert _heartbeats(out) == ["recovering"]


def test_lock_contention_observes_instead_of_acting(tmp_path: Path, watchdog: str) -> None:
    """A second supervisor must never race the first one into a destructive action."""
    passes = 16  # the "another supervisor holds the lock" log fires at n % 8 == 0
    out = _run(
        tmp_path,
        _state(
            http_ok=str(FE_PORT),
            status="healthy",
            uptime=YOUNG_LAUNCHER,
            lock_available=False,
            ALPHA_WD_PASSES=str(passes),
        ),
    )
    assert _actions(out) == []
    assert _heartbeats(out) == ["deferring"] * passes
    assert "Another supervisor holds" in out["RESULT_LOG"]
    assert out["RESULT_LOCK_TAKEN"] == "0"


def test_a_cooldown_pass_returns_instead_of_acting(tmp_path: Path, watchdog: str) -> None:
    """Regression for the merged `... -Stack $s.Summary   return` line.

    PowerShell parses a bare ``return`` trailing another statement on the same
    line as a positional ARGUMENT to that statement, so the cooldown branch
    never returned: it recorded the cooldown heartbeat and fell through into
    Tier 2, restarting the component again immediately. The second pass must
    stop at ``cooldown`` with exactly one action recorded overall.
    """
    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="2",
        ),
    )
    assert _heartbeats(out) == ["recovering", "cooldown"], out
    assert _actions(out) == ["component:gateway"], "the cooldown pass must not act again"


def test_a_compiling_frontend_is_deferred_not_killed(tmp_path: Path, watchdog: str) -> None:
    """A bound port with a still-advancing build log is progress, not a failure."""
    out = _run(
        tmp_path,
        _state(
            ports=BOTH_PORTS,
            http_ok=str(GW_PORT),  # frontend bound, not yet answering
            status="starting",
            uptime=_old_launcher(watchdog),
            log_advancing=True,
            ALPHA_WD_PASSES="1",
        ),
    )
    assert _actions(out) == [], "a frontend that is still compiling must not be restarted"
    assert _heartbeats(out) == ["deferring"]
    assert out["RESULT_LOCK_RELEASED"] == "1"


@pytest.fixture(scope="module")
def watchdog() -> str:
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")
    return WATCHDOG.read_text(encoding="utf-8-sig")
