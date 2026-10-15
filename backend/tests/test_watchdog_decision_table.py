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

The 2026-10-14 restart storm added a second, harder case. ``recovery/watchdog.log``
records three full-stack spawns inside two minutes (22:05:50, 22:06:34, 22:07:45)
against a stack that needs minutes to boot, from two independent causes:

1. A Tier-2 restart that freed **nothing** - ``Stop-PortTree`` returning
   ``already-free`` - still consumed ``MaxComponentRecoveries``. Three vacuous
   passes exhausted the budget and escalated a booting stack (22:05:35).
2. A freshly spawned launcher is honestly **invisible** for 45-70 s, and the
   Tier-3 gate defers only for a *visible* launcher, so each pass re-spawned the
   stack the previous pass had just created.

Both are window bugs: the watchdog acted on a state that was true and
meaningless. Both are reproduced here. Reproducing them needs the harness to
control time - ``ALPHA_WD_PASS_GAP_SECONDS`` back-dates the action and spawn
clocks between passes - because passes that complete in milliseconds let the 30 s
cooldown swallow the entire scenario.
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
# Launcher visibility is script state, not a constant: a spawned launcher takes
# ~45-70 s to publish alpha.pid and its first heartbeat, so ALPHA_WD_LAUNCHER_AFTER_SPAWN
# models the window in which a freshly spawned stack is honestly invisible.
$script:LauncherUp = ($env:ALPHA_WD_LAUNCHER -eq '1')
$script:HbOk       = ($env:ALPHA_WD_HB -eq '1')
$wdStatus   = $env:ALPHA_WD_STATUS
$lockFree   = ($env:ALPHA_WD_LOCK_HELD -ne '0')

function Test-PortListening { param([int]$Port) return ($ports -contains "$Port") }
function Test-HttpOk { param([int]$Port, [string]$Path) return ($https -contains "$Port") }
function Test-FileRecentlyWritten { param([string]$Path, [int]$WithinSeconds = 120) return $logAdv }
function Get-HealthState { return @{ status = $wdStatus } }
function Test-LauncherAlive { return $script:LauncherUp }
function Test-LauncherHeartbeatFresh { param($Health) return $script:HbOk }
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

# Restart-Component reports what the kill actually did. "killed" freed the port,
# "already-free" was a no-op (nothing was bound to kill), "stuck" failed to free
# it. The decision table's budget must be charged differently for each, which is
# only observable if the stub returns the outcome. One value per call, last
# repeats; default is the honest "it freed the port".
$restartOutcomes = @()
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_RESTART_OUTCOME)) {
    $restartOutcomes = @($env:ALPHA_WD_RESTART_OUTCOME -split ',')
}
$script:RestartIdx = 0
function Next-RestartOutcome {
    if ($restartOutcomes.Count -eq 0) { return "killed" }
    $i = $script:RestartIdx
    if ($i -ge $restartOutcomes.Count) { $i = $restartOutcomes.Count - 1 }
    $script:RestartIdx = $i + 1
    return [string]$restartOutcomes[$i]
}
function Restart-Component { param([string]$Component) $script:ActionLog += "component:$Component"; return (Next-RestartOutcome) }
function Start-AlphaStack {
    param([string]$Reason)
    $script:ActionLog += "stack"
    # Model the launcher becoming visible once the spawn has actually produced one.
    if ($env:ALPHA_WD_LAUNCHER_AFTER_SPAWN -eq '1') {
        $script:LauncherUp = $true
        $script:HbOk = $true
    }
    return $true
}

# ---- optional seeding -----------------------------------------------------
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_N))        { $script:ConsecutiveFailures = [int]$env:ALPHA_WD_SEED_N }
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_DEFER))    { $script:DeferPasses = [int]$env:ALPHA_WD_SEED_DEFER }
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_ATTEMPTS)) { $script:ComponentAttempts = @{ gateway = [int]$env:ALPHA_WD_SEED_ATTEMPTS } }
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_SPAWN_AGE)) {
    $script:LastStackSpawnUtc = [DateTime]::UtcNow.AddSeconds(-([double]$env:ALPHA_WD_SEED_SPAWN_AGE))
}
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_SEED_BACKOFF)) {
    $script:ActionBackoff = [int]$env:ALPHA_WD_SEED_BACKOFF
    $script:LastActionUtc = [DateTime]::UtcNow
}
if ($env:ALPHA_WD_SEED_MAINT -eq '1') { $script:MaintenanceLogged = $true }

# ---- driver ---------------------------------------------------------------
$passes = 1
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_PASSES)) { $passes = [int]$env:ALPHA_WD_PASSES }
$clearCooldown = ($env:ALPHA_WD_CLEAR_COOLDOWN -eq '1')

# The shipped loop sleeps $CheckIntervalSeconds between passes, so its cooldown
# and its post-spawn boot grace both age in wall-clock time. Harness passes here
# complete in milliseconds, which would make the 30 s cooldown mask every
# multi-pass scenario and silently hide a restart storm. This back-dates BOTH
# action and spawn clocks by a chosen number of seconds before each pass, so a
# cooldown of N seconds and a grace of M seconds are genuinely exercised.
$passGap = 0.0
if (-not [string]::IsNullOrEmpty($env:ALPHA_WD_PASS_GAP_SECONDS)) {
    $passGap = [double]$env:ALPHA_WD_PASS_GAP_SECONDS
}
for ($p = 1; $p -le $passes; $p++) {
    if ($clearCooldown) { $script:LastActionUtc = $null; $script:ActionBackoff = 0 }
    if ($passGap -gt 0) {
        if ($script:LastActionUtc)    { $script:LastActionUtc    = $script:LastActionUtc.AddSeconds(-$passGap) }
        if ($script:LastStackSpawnUtc) { $script:LastStackSpawnUtc = $script:LastStackSpawnUtc.AddSeconds(-$passGap) }
    }
    Invoke-HealthCheck
}

$attempts = 0
if ($script:ComponentAttempts.Count -gt 0) {
    $m = ($script:ComponentAttempts.Values | Measure-Object -Maximum).Maximum
    if ($null -ne $m) { $attempts = [int]$m }
}
$noop = 0
if ($script:ComponentNoopPasses -and $script:ComponentNoopPasses.Count -gt 0) {
    $m = ($script:ComponentNoopPasses.Values | Measure-Object -Maximum).Maximum
    if ($null -ne $m) { $noop = [int]$m }
}
Write-Output ("RESULT_HEARTBEATS=" + [string]::Join(',', $script:Heartbeats))
Write-Output ("RESULT_ACTIONS=" + [string]::Join(',', $script:ActionLog))
Write-Output ("RESULT_LOG=" + [string]::Join(' | ', $script:LogLines))
Write-Output ("RESULT_CONSECUTIVE=" + $script:ConsecutiveFailures)
Write-Output ("RESULT_DEFER=" + $script:DeferPasses)
Write-Output ("RESULT_ATTEMPTS=" + $attempts)
Write-Output ("RESULT_NOOP=" + $noop)
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
        # Generous: the harness itself does no I/O (every probe and every
        # destructive action is stubbed), so the cost is PowerShell start-up.
        # On a loaded host - several suites plus live gateways - start-up alone
        # has exceeded 120 s, and a timeout there is a harness artefact that
        # reads exactly like a decision failure.
        timeout=300,
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


def test_a_healthy_claim_with_a_starting_gateway_is_restarted_after_hung_threshold(tmp_path: Path, watchdog: str) -> None:
    """A healthy launcher claim cannot defer forever after the Gateway is proven hung.

    The launcher claims ``status=healthy`` while the gateway port is bound but
    ``/health/ready`` has not answered yet - exactly the state that produced
    "escalation after 28 failing checks" and the full-restart loop. The
    healthy-claim budget defers first; after the Gateway's independent probe
    threshold proves it hung, Tier 2 restarts only that component.
    """
    passes = _budget(watchdog, "GatewayHungThreshold") + 2
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
    assert "stack" not in _actions(out), "a healthy launcher should recover only the hung component"
    assert out["RESULT_DEFER"] == "0", "the component restart ends the deferral streak"


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


def test_a_hung_gateway_bypasses_degraded_launcher_deferral(tmp_path: Path, watchdog: str) -> None:
    """A live launcher must not suppress recovery after the Gateway is proven hung.

    The launcher heartbeat can remain fresh while its monitor reports degraded.
    Once the Gateway's own failure threshold classifies it as hung, restart that
    component immediately instead of waiting for the launcher's long grace cap.
    """
    passes = _budget(watchdog, "GatewayHungThreshold") + 2
    out = _run(
        tmp_path,
        _state(
            http_ok=str(FE_PORT),
            status="degraded",
            uptime=1200,  # below the launcher's 3x startup grace cap
            ALPHA_WD_PASSES=str(passes),
        ),
    )
    assert _actions(out) == ["component:gateway"], out
    assert _heartbeats(out).count("recovering") == 1
    assert "stack" not in _actions(out)


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


# ===========================================================================
# The 2026-10-14 restart storm: two independent defects, one measured incident
# ===========================================================================
#
# recovery/watchdog.log on this machine, 2026-10-14 22:01 -> 22:08:
#
#   22:01:16 gateway :: component_restart -> noop   (port 8001 already free ...)
#   22:01:48 gateway :: component_restart -> noop   (port 8001 already free ...)
#   22:04:47 gateway :: component_restart -> noop   (port 8001 already free ...)
#   22:05:35 [WARN] gateway exceeded 3 component restarts - escalating to full stack
#   22:05:50 stack   :: full_restart -> spawned
#   22:06:34 stack   :: full_restart -> spawned
#   22:07:45 stack   :: full_restart -> spawned
#
# Two causes, and both must be fixed or the storm returns:
#
#   (1) A Tier-2 restart that frees NOTHING still consumed MaxComponentRecoveries.
#       Three vacuous "already free before kill" passes exhausted the budget while
#       the launcher was booting a gateway that had not bound its port yet
#       (uvicorn holds no listener for the whole 2-3 min import). The 4th pass hit
#       "budget exhausted", which is one of the Tier-3 gate's two permitted cases,
#       and killed a stack that was booting normally.
#
#   (2) A newly spawned stack is honestly INVISIBLE for 45-70 s - start.ps1 writes
#       alpha.pid and its first heartbeat only after it has taken over the ports
#       and spawned the children. Get-StackSnapshot reports launcher=dead in that
#       window, the Tier-3 defer gate requires LauncherAlive, so every check
#       escalated again: the first "stack" line above is immediately followed by
#       two more, each killing the stack the watchdog had just created.
#
# Both are window bugs: the watchdog acted on a state that was true and meaningless.

# Measured on this machine from the log above, not estimated: the gap between one
# `stack :: full_restart` line and the next was 44 s and 71 s. That is how long the
# watchdog believed the launcher was dead after spawning it.
MEASURED_LAUNCHER_INVISIBILITY_SECONDS = 70


def test_a_post_spawn_boot_grace_stops_the_full_stack_restart_storm(tmp_path: Path, watchdog: str) -> None:
    """One spawn, then observe - never re-spawn while the stack is still booting.

    The pass gap is chosen strictly between the generic cooldown and the boot
    grace, which is the band in which the incident happened: 30 s of cooldown
    expires long before the 45-70 s invisibility window does, so every check
    reached the Tier-3 gate and the gate - which only defers for a VISIBLE
    launcher - let it straight through.
    """
    cooldown = _budget(watchdog, "ActionBackoffStart")
    grace = _budget(watchdog, "StackBootGraceSeconds")
    gap = cooldown + 30
    assert cooldown < gap < grace, f"the storm window is the gap between the cooldown and the boot grace; cooldown={cooldown}s grace={grace}s cannot express it"

    out = _run(
        tmp_path,
        _state(
            ports="",
            http_ok="",
            status="starting",
            launcher=0,  # the freshly spawned launcher is still invisible
            hb=0,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="3",
            ALPHA_WD_PASS_GAP_SECONDS=str(gap),
        ),
    )

    assert _actions(out) == ["stack"], f"exactly one full-stack spawn, then observe - got {_actions(out)} (log: {out['RESULT_LOG']})"
    assert _heartbeats(out) == ["recovering", "boot-grace", "boot-grace"], out
    assert out["RESULT_CONSECUTIVE"] == "0", "boot-grace is a deferral: it must not feed ConsecutiveFailures"
    assert out["RESULT_DEFER"] == "2", "boot-grace passes count against the deferral streak"
    assert "Boot grace" in out["RESULT_LOG"], "the grace must leave a diagnostic line, not pass silently"


def test_the_boot_grace_window_is_bounded_so_a_dead_launcher_still_escalates(tmp_path: Path, watchdog: str) -> None:
    """An unbounded grace would just trade the storm for a permanent no-op.

    The window must expire. Past it the decision is the shipped one again: the
    launcher is dead, so nobody can act and the whole stack is replaced.
    """
    grace = _budget(watchdog, "StackBootGraceSeconds")
    gap = grace + 60

    out = _run(
        tmp_path,
        _state(
            ports="",
            http_ok="",
            status="starting",
            launcher=0,
            hb=0,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="3",
            ALPHA_WD_PASS_GAP_SECONDS=str(gap),
        ),
    )

    assert "boot-grace" not in _heartbeats(out), out
    assert _actions(out) == ["stack", "stack", "stack"], out


def test_boot_grace_never_suppresses_a_component_restart_while_the_launcher_is_visible(tmp_path: Path, watchdog: str) -> None:
    """The grace is scoped to an INVISIBLE launcher and must stay that way.

    Pass 1 finds a dead launcher and spawns a replacement; the launcher then
    becomes visible. Pass 2 must reach Tier 2 and restart the dead gateway. A
    grace wide enough to apply to a visible launcher would suppress the one
    recovery that is legitimately still available.
    """
    gap = _budget(watchdog, "ActionBackoffStart") + 30
    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),  # gateway down
            http_ok=str(FE_PORT),
            status="starting",
            launcher=0,
            hb=0,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_LAUNCHER_AFTER_SPAWN="1",
            ALPHA_WD_PASSES="2",
            ALPHA_WD_PASS_GAP_SECONDS=str(gap),
        ),
    )

    assert _actions(out) == ["stack", "component:gateway"], out
    assert "boot-grace" not in _heartbeats(out), out


def test_boot_grace_applies_only_after_this_watchdog_actually_spawned(tmp_path: Path, watchdog: str) -> None:
    """A spawn recorded by a PREVIOUS pass is what opens the window.

    Seeding the spawn clock directly proves the grace is keyed to the recorded
    spawn time rather than to "the launcher looks dead", which would make the
    watchdog wait out a grace window on the very first pass it ever sees a
    machine with no launcher - the state where recovery is most urgent.
    """
    grace = _budget(watchdog, "StackBootGraceSeconds")

    for name in ("fresh", "stale", "inside"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    fresh = _run(
        tmp_path / "fresh",
        _state(
            ports="",
            http_ok="",
            status="starting",
            launcher=0,
            hb=0,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="1",
        ),
    )
    stale = _run(
        tmp_path / "stale",
        _state(
            ports="",
            http_ok="",
            status="starting",
            launcher=0,
            hb=0,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="1",
            ALPHA_WD_SEED_SPAWN_AGE=str(grace + 60),
        ),
    )
    inside = _run(
        tmp_path / "inside",
        _state(
            ports="",
            http_ok="",
            status="starting",
            launcher=0,
            hb=0,
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES="1",
            ALPHA_WD_SEED_SPAWN_AGE="10",
        ),
    )

    assert _actions(fresh) == ["stack"], "a first-ever pass must act at once"
    assert _actions(stale) == ["stack"], "an expired window must act"
    assert _actions(inside) == [], "inside the window the watchdog observes instead"
    assert _heartbeats(inside) == ["boot-grace"]


def _pass_gap(watchdog: str) -> str:
    """The real inter-pass interval, as a harness back-date.

    Tier-2 arms a 15 s cooldown and the shipped loop then sleeps
    ``$CheckIntervalSeconds``, so consecutive Tier-2 passes are one check
    interval apart. Without a gap the harness sees ~0 s of elapsed time and every
    pass after the first is swallowed by the cooldown.
    """
    return str(_budget(watchdog, "CheckIntervalSeconds"))


def test_a_noop_component_restart_does_not_consume_the_restart_budget(tmp_path: Path, watchdog: str) -> None:
    """The 22:05:35 escalation, reproduced: a kill that freed nothing is not a kill.

    ``Stop-PortTree`` returns "already-free" when nothing was listening - which is
    exactly what a gateway looks like for the whole of its 2-3 minute import.
    Charging that against ``MaxComponentRecoveries`` made three vacuous passes
    exhaust the budget and hand the Tier-3 gate its "budget exhausted" case,
    destroying a stack that was booting normally.
    """
    max_components = _budget(watchdog, "MaxComponentRecoveries")
    passes = max_components + 2

    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),  # gateway port closed: nothing to kill
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES=str(passes),
            ALPHA_WD_PASS_GAP_SECONDS=_pass_gap(watchdog),
            ALPHA_WD_RESTART_OUTCOME="already-free",
        ),
    )

    assert _actions(out) == ["component:gateway"] * passes, out
    assert "stack" not in _actions(out), "a launcher that is still booting must not be escalated past"
    assert out["RESULT_ATTEMPTS"] == "0", "a no-op freed nothing and must not be charged as a restart"
    assert int(out["RESULT_NOOP"]) == passes, "it is charged against the no-progress streak instead"


def test_a_stuck_kill_still_consumes_the_restart_budget(tmp_path: Path, watchdog: str) -> None:
    """The mirror of the no-op rule: a kill that FAILS is a real failed attempt.

    "already-free" means nothing was there; "stuck" means a bound port refused to
    die. Only the second is evidence that the component needs replacing.
    """
    (tmp_path / "charged").mkdir(parents=True, exist_ok=True)
    max_components = _budget(watchdog, "MaxComponentRecoveries")

    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES=str(max_components + 1),
            ALPHA_WD_PASS_GAP_SECONDS=_pass_gap(watchdog),
            ALPHA_WD_RESTART_OUTCOME="stuck",
        ),
    )
    # Charged, and charged all the way: three failed kills, then the escalation.
    assert _actions(out) == ["component:gateway"] * max_components + ["stack"], out

    # One pass short of the escalation the counter is still readable, because the
    # full-stack step resets it - which is why the escalation above is asserted
    # through the action list rather than the final counter value.
    charged = _run(
        tmp_path / "charged",
        _state(
            ports=str(FE_PORT),
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES=str(max_components),
            ALPHA_WD_PASS_GAP_SECONDS=_pass_gap(watchdog),
            ALPHA_WD_RESTART_OUTCOME="stuck",
        ),
    )
    assert charged["RESULT_ATTEMPTS"] == str(max_components)
    assert charged["RESULT_NOOP"] == "0", "a failed kill is not a no-op"


def test_a_permanently_dead_component_still_escalates_after_the_no_progress_bound(tmp_path: Path, watchdog: str) -> None:
    """Not charging no-ops must not become "never escalate".

    If the launcher claims to be working forever while a port stays closed, the
    port-tree kill can never do anything - every pass is a no-op. Escalation then
    has to come from a counter of its own, and that counter has to be bounded.
    """
    noop_bound = _budget(watchdog, "MaxComponentNoopPasses")
    passes = noop_bound + 1

    out = _run(
        tmp_path,
        _state(
            ports=str(FE_PORT),
            http_ok=str(FE_PORT),
            status="starting",
            uptime=_old_launcher(watchdog),
            ALPHA_WD_PASSES=str(passes),
            ALPHA_WD_PASS_GAP_SECONDS=_pass_gap(watchdog),
            ALPHA_WD_RESTART_OUTCOME="already-free",
        ),
    )

    assert _actions(out) == ["component:gateway"] * noop_bound + ["stack"], out
    assert "no progress" in out["RESULT_LOG"], f"the escalation must name its real reason, got {out['RESULT_LOG']!r}"
    assert out["RESULT_ATTEMPTS"] == "0", "the escalation came from the no-progress streak, not a restart budget"


def test_boot_grace_covers_the_measured_launcher_invisibility_window(watchdog: str) -> None:
    """A window shorter than the measured window does not fix the incident."""
    grace = _budget(watchdog, "StackBootGraceSeconds")
    assert grace > MEASURED_LAUNCHER_INVISIBILITY_SECONDS, (
        f"$StackBootGraceSeconds is {grace}s but a spawned launcher was measured invisible for "
        f"{MEASURED_LAUNCHER_INVISIBILITY_SECONDS}s (watchdog.log 2026-10-14: three full_restart "
        f"spawns 44s and 71s apart). The window must outlast the state it exists to absorb."
    )


def test_boot_grace_outlives_the_generic_action_cooldown(watchdog: str) -> None:
    """The cooldown was never the thing standing between passes.

    After a full-stack spawn ``$ActionBackoffStart`` (30 s) is the cooldown in
    force, and it is SHORTER than the boot window - which is why it never stopped
    the storm. The doubling backoff eventually outgrows the boot window on its
    own, which is exactly why the storm self-limited at three spawns, but that is
    luck rather than a bound. The grace is the deliberate, separately-budgeted
    answer, and it must start above the cooldown it replaces in this window.
    """
    grace = _budget(watchdog, "StackBootGraceSeconds")
    cooldown_start = _budget(watchdog, "ActionBackoffStart")
    assert grace > cooldown_start, (
        f"$StackBootGraceSeconds={grace}s must exceed $ActionBackoffStart={cooldown_start}s, the cooldown in force immediately after a spawn; a grace inside that window is indistinguishable from the cooldown it was added to fix"
    )


def test_boot_grace_stays_below_the_launcher_startup_grace(watchdog: str) -> None:
    """Bounded above by the launcher's own first-boot patience, not above it.

    The grace absorbs launcher INVISIBILITY; it must never be longer than the
    period the launcher itself is entitled to boot for, or the watchdog is
    supervising a boot it has already declared over.
    """
    grace = _budget(watchdog, "StackBootGraceSeconds")
    startup_grace = _budget(watchdog, "StartupGraceSeconds")
    assert grace < startup_grace, f"$StackBootGraceSeconds={grace}s exceeds $StartupGraceSeconds={startup_grace}s: the watchdog would still be waiting out the grace after it had already declared the launcher's first boot over"


@pytest.fixture(scope="module")
def watchdog() -> str:
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")
    return WATCHDOG.read_text(encoding="utf-8-sig")
