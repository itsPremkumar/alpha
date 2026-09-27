"""The launcher and the watchdog must agree on how long a component may boot.

`start.ps1` is the process that actually starts the Gateway and the frontend and
waits a bounded time for each to become healthy. `scripts/watchdog.ps1` runs
alongside it as a supervisor and will restart a component it believes is hung.

If the supervisor's patience is not strictly greater than the budget the
supervised process is waiting on, the supervisor kills a component that is still
legitimately booting, and the stack can never converge. That is not theoretical:
with the gateway threshold at 6 x 30 s = 180 s against `start.ps1`'s
`MaxWaitSeconds 240`, the watchdog aborted every boot 60 s early and the UI was
stuck reporting "starting ... waiting for services (gateway=False ...)" through
launcher attempt 54/450.

These tests read the two real scripts and assert the ordering, so a future edit
to either side that breaks the contract fails here instead of in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "scripts" / "watchdog.ps1"
LAUNCHER = REPO_ROOT / "start.ps1"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.name} is not present in this checkout")
    # These are PowerShell 5.1 scripts that intentionally carry a UTF-8 BOM;
    # decode as utf-8-sig so the BOM is not mistaken for content.
    return path.read_text(encoding="utf-8-sig")


def _ps_int(source: str, name: str) -> int:
    match = re.search(rf"^\s*\${re.escape(name)}\s*=\s*(\d+)\s*(?:#.*)?$", source, re.MULTILINE)
    assert match, f"could not find ${name} = <int> — the watchdog contract changed shape"
    return int(match.group(1))


def _launcher_budget(source: str, port_var: str) -> int:
    """Read `Wait-ForHealthy ... -Port $<port_var> ... -MaxWaitSeconds N`."""
    pattern = rf"Wait-ForHealthy\s+-Port\s+\${port_var}\b[^\n]*?-MaxWaitSeconds\s+(\d+)"
    match = re.search(pattern, source)
    assert match, f"could not find the -MaxWaitSeconds budget for ${port_var}"
    return int(match.group(1))


@pytest.fixture(scope="module")
def watchdog() -> str:
    return _read(WATCHDOG)


@pytest.fixture(scope="module")
def launcher() -> str:
    return _read(LAUNCHER)


@pytest.mark.parametrize(
    ("component", "threshold_var", "port_var"),
    [
        ("gateway", "GatewayHungThreshold", "GatewayPort"),
        ("frontend", "FrontendHungThreshold", "FrontendPort"),
    ],
)
def test_supervisor_patience_exceeds_the_launcher_budget(
    component: str, threshold_var: str, port_var: str, watchdog: str, launcher: str
) -> None:
    """The watchdog must never kill a component before start.ps1 gives up on it.

    This is the invariant whose violation caused the permanent restart loop: the
    watchdog restarted a still-booting gateway 60 s before the launcher's own
    240 s patience expired, so the launcher's success path was unreachable.
    """
    interval = _ps_int(watchdog, "CheckIntervalSeconds")
    threshold = _ps_int(watchdog, threshold_var)
    budget = _launcher_budget(launcher, port_var)

    watchdog_budget = threshold * interval
    assert watchdog_budget > budget, (
        f"{component}: scripts/watchdog.ps1 gives up after {watchdog_budget}s "
        f"(${threshold_var}={threshold} x ${'CheckIntervalSeconds'}={interval}) but start.ps1 "
        f"waits {budget}s (${port_var} MaxWaitSeconds). The watchdog would kill a component "
        f"that start.ps1 is still legitimately waiting for, so the stack can never converge. "
        f"${threshold_var} must satisfy {threshold_var} * CheckIntervalSeconds > {budget}."
    )


def test_check_interval_is_positive(watchdog: str) -> None:
    """A zero/negative interval would make the thresholds above meaningless."""
    assert _ps_int(watchdog, "CheckIntervalSeconds") > 0


# Measured on this machine, not estimated: a cold Next.js dev compile of "/"
# logged "Compiled / in 880.1s (1710 modules)" while the launcher sat at
# attempt 189/450. The launcher's frontend budget is a COLD-COMPILE budget --
# Next.js binds its port before it finishes compiling -- so it must stay above
# the slowest compile actually observed, or the launcher gives up and relaunches
# the frontend, discarding the compile and never converging.
MEASURED_FRONTEND_COLD_COMPILE_SECONDS = 880


def test_frontend_budget_covers_the_measured_cold_compile(launcher: str) -> None:
    budget = _launcher_budget(launcher, "FrontendPort")
    assert budget > MEASURED_FRONTEND_COLD_COMPILE_SECONDS, (
        f"start.ps1 waits {budget}s for the frontend but a cold compile was measured at "
        f"{MEASURED_FRONTEND_COLD_COMPILE_SECONDS}s ('Compiled / in 880.1s (1710 modules)'). "
        f"A smaller budget makes the launcher relaunch the frontend mid-compile, throwing away "
        f"the build and looping forever. Raise MaxWaitSeconds above the measured worst case."
    )


def test_watchdog_does_not_treat_a_compiling_frontend_as_hung(watchdog: str) -> None:
    """Progress-awareness must exist, or a compiling frontend gets killed.

    Next.js binds its port before compiling, so "port open, HTTP not answering"
    is ambiguous. The watchdog must consult the build log before declaring the
    frontend hung and restarting it.
    """
    assert "Test-FileRecentlyWritten" in watchdog, (
        "scripts/watchdog.ps1 must use Test-FileRecentlyWritten to avoid killing a frontend "
        "that is still compiling"
    )
    assert "compiling" in watchdog, (
        "the frontend snapshot must report a distinct 'compiling' state so it is not "
        "classified as 'hung' (which triggers a restart mid-compile)"
    )


def test_watchdog_retains_its_utf8_bom() -> None:
    """PowerShell 5.1 requires the BOM on these scripts; a stripped BOM breaks them."""
    if not WATCHDOG.exists():
        pytest.skip("scripts/watchdog.ps1 is not present in this checkout")
    assert WATCHDOG.read_bytes().startswith(b"\xef\xbb\xbf"), (
        "scripts/watchdog.ps1 lost its UTF-8 BOM — PowerShell 5.1 needs it"
    )
