"""The watchdog must reap EVERY live launcher, not just the one in the PID file.

`recovery/watchdog.ps1:Stop-StaleLauncher` used to read a single PID from
`logs/alpha.pid` and kill that. A launcher that started before the file was last
written -- or one whose entry a competing launcher had already overwritten --
was therefore invisible, and two launchers coexisted.

That is fatal rather than cosmetic, because each launcher's startup clears
whatever holds ports 8001/3000. Two launchers alternately kill each other's
gateway, so port 8001 is never stably bound and the tray reports
"waiting for services (gateway=False frontend=False)" forever. Measured live on
this machine: launchers 4140 and 15500 both running a gateway while
`logs/alpha.pid` named only 15500.

This test runs the real function out of the real script against a real decoy
process whose command line looks like a launcher, and asserts the decoy is
reaped even though the PID file points somewhere else entirely.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "recovery" / "watchdog.ps1"
START_PS1 = REPO_ROOT / "start.ps1"

POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _function_body(name: str) -> str:
    """Extract a top-level `function <name> { ... }` from the watchdog script."""
    text = WATCHDOG.read_text(encoding="utf-8-sig")
    start = text.index(f"function {name} {{")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"could not extract {name}() from watchdog.ps1")


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return str(pid) in out


def test_stop_stale_launcher_does_not_rely_only_on_the_pid_file() -> None:
    """A structural guard: the sweep must enumerate launchers, not trust one PID."""
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")
    body = _function_body("Stop-StaleLauncher")
    assert "Get-CimInstance" in body, (
        "Stop-StaleLauncher must enumerate running processes to find launchers; "
        "reading only logs/alpha.pid leaves unrecorded launchers alive"
    )
    assert "CommandLine" in body, "the sweep must match on the launcher's command line"
    assert "watchdog" in body, "the sweep must exclude the watchdog itself so it cannot kill itself"


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not on PATH")
def test_stop_stale_launcher_reaps_an_unrecorded_second_launcher(tmp_path: Path) -> None:
    """The behavioural proof: a live launcher the PID file does not name is killed.

    A decoy is started whose command line contains the real start.ps1 path, so it
    is indistinguishable from a real launcher to the sweep. The PID file is then
    pointed at a PID that is not running, which is exactly the state that let the
    duplicate survive in production.
    """
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")

    decoy = subprocess.Popen(
        [
            POWERSHELL, "-NoProfile", "-Command",
            f"# decoy launcher for {START_PS1}\nStart-Sleep -Seconds 300",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Give the OS a moment to register the process command line.
        for _ in range(20):
            time.sleep(0.25)
            listing = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={decoy.pid}", "get", "CommandLine"],
                capture_output=True, text=True, check=False,
            ).stdout if shutil.which("wmic") else ""
            if str(START_PS1) in listing or not listing:
                break

        pid_file = tmp_path / "alpha.pid"
        pid_file.write_text("999999", encoding="utf-8")  # a PID that is not running

        script = f"""
$ErrorActionPreference = "SilentlyContinue"
$AlphaPid   = "{pid_file}"
$StartScript = "{START_PS1}"
$PID        = $PID
function Get-PidFileValue {{ param([string]$Path) if (-not (Test-Path $Path)) {{ return 0 }} try {{ return [int](Get-Content $Path -Raw) }} catch {{ return 0 }} }}
{_function_body("Stop-StaleLauncher")}
Stop-StaleLauncher
"""
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=120, check=False,
        )
        assert result.returncode == 0, f"sweep failed: {result.stderr[:400]}"

        for _ in range(20):
            if not _alive(decoy.pid):
                break
            time.sleep(0.5)

        assert not _alive(decoy.pid), (
            f"Stop-StaleLauncher left an unrecorded launcher (PID {decoy.pid}) running. "
            "Two live launchers kill each other's gateway on ports 8001/3000, so the "
            "stack can never come up."
        )
    finally:
        if _alive(decoy.pid):
            subprocess.run(["taskkill", "/PID", str(decoy.pid), "/T", "/F"],
                           capture_output=True, check=False)
        try:
            decoy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


# --------------------------------------------------------------------------- #
# supervisor exclusivity
# --------------------------------------------------------------------------- #
def test_watchdog_has_a_supervisor_lock() -> None:
    """A second watchdog must be able to detect that it may not act.

    Without this, the persistent loop and a `watchdog.ps1 -Once` pass both
    escalate on the same unhealthy stack and alternately rebuild it.
    """
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")
    text = WATCHDOG.read_text(encoding="utf-8-sig")
    assert "Enter-SupervisorLock" in text, "the watchdog must gate destructive actions on a lock"
    assert "FileShare]::None" in text, (
        "the supervisor lock must be an exclusive open (FileShare.None) so the OS "
        "releases it if the holder dies"
    )


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not on PATH")
def test_supervisor_lock_is_exclusive_across_processes(tmp_path: Path) -> None:
    """Behavioural: while one process holds the lock, a second one cannot take it."""
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")

    lock = tmp_path / "watchdog.lock"
    holder_body = f"""
$fs = [System.IO.File]::Open("{lock}", [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
Start-Sleep -Seconds 8
$fs.Close()
"""
    holder = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-Command", holder_body],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            time.sleep(0.25)
            if lock.exists():
                break

        probe = f"""
try {{
    $fs = [System.IO.File]::Open("{lock}", [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    Write-Output "ACQUIRED"
    $fs.Close()
}} catch {{
    Write-Output "BLOCKED"
}}
"""
        out = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command", probe],
            capture_output=True, text=True, timeout=60, check=False,
        ).stdout
        assert "BLOCKED" in out, (
            f"a second supervisor acquired the lock while another held it (got {out.strip()!r}); "
            "two watchdogs would then race to rebuild the stack"
        )
    finally:
        if _alive(holder.pid):
            subprocess.run(["taskkill", "/PID", str(holder.pid), "/T", "/F"],
                           capture_output=True, check=False)
        try:
            holder.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def test_watchdog_requests_the_production_frontend_when_a_build_exists() -> None:
    """`next dev` cold-compiles for ~880 s; a build lets `next start` serve at once.

    start.ps1 already implements -Prod (build once if .next/BUILD_ID is missing,
    then `next start`). The watchdog has to actually pass it, otherwise every
    boot pays the dev compile again.
    """
    if not WATCHDOG.exists():
        pytest.skip("recovery/watchdog.ps1 is not present in this checkout")
    text = WATCHDOG.read_text(encoding="utf-8-sig")
    start_body = text[text.index("function Start-AlphaStack") :]
    start_body = start_body[: start_body.index("\nfunction ")] if "\nfunction " in start_body else start_body
    assert "BUILD_ID" in start_body, (
        "Start-AlphaStack must detect a production build via .next/BUILD_ID"
    )
    assert "-Prod" in start_body, "Start-AlphaStack must pass -Prod to start.ps1 when a build exists"
    # Auto-detected, not forced: editing components must still get the dev server.
    assert 'if (Test-Path "$RepoRoot\\frontend\\.next\\BUILD_ID")' in start_body, (
        "-Prod must be conditional on a build being present so HMR still works when editing"
    )
