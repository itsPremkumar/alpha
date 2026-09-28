"""The notification-area indicator must describe the *present*, not the past.

Reproduced on this checkout before any of it was fixed:

    logs/alpha_health.json -> {"status": "starting", "pid": 33252,
                               "timestamp_utc": "...T15:09:09Z"}
    ... eleven hours later, with the live stack answering HTTP 200 on both
    8001/health/ready and 3000/.

``logs/alpha_health.json`` is written only by ``start.ps1``. A stack started any
other way - ``make dev``, ``scripts/serve.sh``, a leftover from an earlier run -
has no writer, so the file on disk describes a launcher that is long gone. And
because ``Get-AlphaState`` ordered its branches ``failed`` -> ``starting`` ->
... -> ``healthy``, the stale ``"starting"`` matched *before* the live HTTP
probes were ever consulted. The indicator therefore sat on "booting services"
indefinitely while both services were up: a permanent, confident lie, in the one
component whose entire job is to be trustworthy about uptime.

These tests run the real ``Get-AlphaState`` out of ``scripts/tray_status.ps1``
under PowerShell, with the two port/HTTP probes replaced by controllable stubs
and the health file pointed at a temp directory. So they exercise the shipped
decision table rather than a description of it, and they fail on the pre-fix
ordering.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAY_PS1 = REPO_ROOT / "scripts" / "tray_status.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="scripts/tray_status.ps1 is the Windows tray")

# PowerShell that loads the evaluator and prints the decision it reaches.
#
# The extraction is positional on purpose: New-StatusIcon and the icon globals
# need System.Drawing, so they are skipped, while the config block, the two
# probe helpers and Get-AlphaState are kept. If the file is ever reordered this
# harness fails loudly at load time instead of silently reporting "stopped" -
# which is exactly the false negative that made this bug hard to see.
HARNESS = r"""
$ErrorActionPreference = 'Stop'
$src = Get-Content -LiteralPath $env:ALPHA_TRAY_SRC -Raw

$skipFrom  = $src.IndexOf('function New-StatusIcon')
$probeFrom = $src.IndexOf('function Test-PortUp')
$stateFrom = $src.IndexOf('function Get-AlphaState')
if ($skipFrom -lt 0 -or $probeFrom -lt 0 -or $stateFrom -lt 0) {
    Write-Output "HARNESS_BROKEN: markers missing ($skipFrom/$probeFrom/$stateFrom)"
    exit 3
}
$stateEnd = $src.IndexOf('# ----', $stateFrom)
if ($stateEnd -lt 0) { $stateEnd = $src.Length }

# Config block, then the read-only probe helpers and the evaluator.
$code = $src.Substring(0, $skipFrom) + $src.Substring($probeFrom, $stateEnd - $probeFrom)
Invoke-Expression $code

# Repoint every derived path at the temp root so the real logs\ dir is never read.
$RepoRoot        = $env:ALPHA_TRAY_ROOT
$LogDir          = $env:ALPHA_TRAY_ROOT
$HealthFile      = Join-Path $LogDir 'alpha_health.json'
$MaintenanceFile = Join-Path $LogDir 'alpha_maintenance.json'
$TrayPidFile     = Join-Path $LogDir 'tray.pid'

# Icons are not constructed headlessly; the decision is the Key, not the bitmap.
$IconHealthy = 'LimeGreen'
$IconWorking = 'Orange'
$IconFailed  = 'Crimson'
$IconStopped = 'DimGray'

# Controllable probes. Test-PortUp is keyed by port; Test-Http200 by URL, so a
# listening-but-not-serving service can be expressed (port up, probe false).
$ports = $env:ALPHA_TRAY_PORTS -split ','
$urls  = $env:ALPHA_TRAY_HTTP_OK -split ','
function Test-PortUp { param([int]$Port) return ($ports -contains "$Port") }
function Test-Http200 { param([string]$Url) return ($urls -contains $Url) }

$state = Get-AlphaState
Write-Output ("KEY=" + $state.Key)
Write-Output ("TOOLTIP=" + $state.ToolTip)
Write-Output ("ICON=" + $state.Icon)
"""

GW_URL = "http://127.0.0.1:8001/health/ready"
FE_URL = "http://127.0.0.1:3000/"


def _run_state(
    root: Path,
    *,
    health: dict | None,
    ports: str = "8001,3000",
    http_ok: str = f"{GW_URL},{FE_URL}",
) -> dict:
    """Execute the tray's real Get-AlphaState and return its decision."""
    if health is not None:
        (root / "alpha_health.json").write_text(json.dumps(health), encoding="utf-8")

    harness = root / "harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "ALPHA_TRAY_SRC": str(TRAY_PS1),
            "ALPHA_TRAY_ROOT": str(root),
            "ALPHA_TRAY_PORTS": ports,
            "ALPHA_TRAY_HTTP_OK": http_ok,
        }
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )
    assert "HARNESS_BROKEN" not in proc.stdout, (
        f"tray_status.ps1 was reordered; the extraction harness no longer finds its markers.\n{proc.stdout}\n{proc.stderr}"
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    assert "KEY" in out, f"harness produced no decision\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    return out


def _health(status: str, *, age_seconds: int, pid: int) -> dict:
    when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "status": status,
        "pid": pid,
        "timestamp_utc": when.isoformat().replace("+00:00", "Z"),
    }


def test_harness_evaluates_the_real_tray_decision(tmp_path: Path) -> None:
    """Sanity anchor: the harness drives the shipped code, not a copy of it."""
    root = tmp_path / "live"
    root.mkdir()
    state = _run_state(root, health=None)
    assert state["KEY"] == "healthy", state
    assert state["ICON"] == "LimeGreen", state


def test_stale_starting_status_does_not_mask_a_healthy_stack(tmp_path: Path) -> None:
    """The reported bug: a long-dead launcher's "starting" must not win.

    The health file names a PID that no longer exists and is hours old, and
    says the stack is still booting. Both services answer HTTP 200. The honest
    answer is healthy, and the tooltip must not claim a stale check happened.
    """
    root = tmp_path / "stale_starting"
    root.mkdir()
    # A PID that cannot be alive: above any plausible process id on Windows.
    state = _run_state(root, health=_health("starting", age_seconds=42_000, pid=999_999))

    assert state["KEY"] == "healthy", state
    assert state["ICON"] == "LimeGreen", state
    assert "live check" in state["TOOLTIP"], state
    assert "starting" not in state["TOOLTIP"].lower(), state


def test_fresh_health_file_with_a_live_pid_may_still_report_starting(tmp_path: Path) -> None:
    """A *trustworthy* health file keeps its authority - the fix is not a blanket ignore.

    The launcher's own PID is the harness's own PID, so the "is the PID alive"
    half of the trust check is satisfied, and the file is new enough. Reporting
    "starting" here is correct: something really did just start.
    """
    root = tmp_path / "fresh_starting"
    root.mkdir()
    state = _run_state(root, health=_health("starting", age_seconds=5, pid=os.getpid()))

    assert state["KEY"] == "working", state
    assert state["ICON"] == "Orange", state
    assert "starting" in state["TOOLTIP"].lower(), state


def test_fresh_health_file_with_a_dead_pid_is_not_trusted(tmp_path: Path) -> None:
    """Freshness alone is not enough; the writer must still exist.

    A launcher killed by a hard Task Manager stop never retracts its status, so
    it can leave a *recent* file claiming a terminal or booting state. The PID
    check is what makes that recoverable.
    """
    root = tmp_path / "fresh_dead_pid"
    root.mkdir()
    state = _run_state(root, health=_health("starting", age_seconds=5, pid=999_999))

    assert state["KEY"] == "healthy", state
    assert state["ICON"] == "LimeGreen", state


def test_stale_file_cannot_keep_a_dead_stack_looking_busy(tmp_path: Path) -> None:
    """The mirror image: a stale "healthy" must not survive a stopped stack.

    Otherwise the file is not merely ignored when wrong, but preferred when
    convenient - the same defect pointing the other way.
    """
    root = tmp_path / "stale_healthy_dead_stack"
    root.mkdir()
    state = _run_state(
        root,
        health=_health("healthy", age_seconds=42_000, pid=999_999),
        ports="",
        http_ok="",
    )

    # "unknown", not "stopped": a health file is present but describes the past,
    # so the honest label is "not running, state unknown" rather than the
    # clean "stopped" the tray uses when it has no evidence at all. Either way
    # the icon is the stopped mark - what must never happen is green.
    assert state["KEY"] in {"unknown", "stopped"}, state
    assert state["ICON"] == "DimGray", state
    assert state["TOOLTIP"].startswith("Alpha - Not running"), state


def test_stale_failed_status_does_not_shadow_a_recovered_stack(tmp_path: Path) -> None:
    """A stale terminal "failed" must not pin the tray red after recovery.

    ``start.ps1``'s ``Fail-Startup`` records a terminal status and exits; nothing
    later retracts it. Once the operator fixes the cause and the stack comes up
    by another route, an unretracted "failed" is the same permanent lie.
    """
    root = tmp_path / "stale_failed"
    root.mkdir()
    state = _run_state(root, health=_health("failed", age_seconds=42_000, pid=999_999))

    assert state["KEY"] == "healthy", state
    assert state["ICON"] == "LimeGreen", state


def test_listening_but_not_serving_is_not_healthy(tmp_path: Path) -> None:
    """HTTP 200 on both is the bar; a bound port alone is not health.

    A process that is listening but wedged - or whose /health/ready reports
    503 because the checkpointer is down - must not be announced as healthy.
    This is the reason the healthy branch requires $gwHttp and not just $gw.
    """
    root = tmp_path / "wedged"
    root.mkdir()
    state = _run_state(
        root,
        health=None,
        ports="8001,3000",
        http_ok="",  # both bound, neither serving
    )

    assert state["KEY"] != "healthy", state
    assert state["ICON"] != "LimeGreen", state
