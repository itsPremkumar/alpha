"""The dev launcher must not advertise an entry point that is not serving.

``scripts/dev_ui.ps1`` starts the *frontend* only. Nginx on port 2026 is brought
up by a separate command (``make nginx`` in the foreground, or ``make up`` for
the Docker stack) and is documented as optional on Windows, where direct dev
mode routes through Next.js (``scripts/doctor.py`` says so in as many words).

The launcher nevertheless printed::

    Full stack: http://127.0.0.1:2026  (nginx proxies here)

unconditionally, immediately after declaring itself READY. On a host with no
nginx that line names a port with nothing behind it, so the very message whose
job is to say "it worked, open this" pointed at a connection-refused page - the
user's own observation of the stack being "down" while every service was up.

Two things are pinned here, and neither is a description of the fix:

1. **Static** - the 2026 sentence is produced only by
   ``Get-AlphaEntryPointAdvice``, whose call site first probes the port. A
   regression reintroducing an unconditional ``Write-Host`` of the 2026 URL
   fails here.
2. **Behavioural** - the real function, extracted from the shipped script, is
   executed for both outcomes. Listening yields the proxy sentence; not
   listening yields the two lines that say which command brings nginx up. A
   function that returns the proxy sentence either way would pass a static pin
   and still lie to the user, which is why this one runs it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_UI = REPO_ROOT / "scripts" / "dev_ui.ps1"

FUNCTION_NAME = "Get-AlphaEntryPointAdvice"
NGINX_PORT = 2026

# The one sentence that must never be printed while nginx is down.
PROXY_SENTENCE_FRAGMENT = "nginx proxies here"


def _powershell() -> str:
    """A PowerShell executable that can run the shipped script."""
    for candidate in ("pwsh", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no PowerShell interpreter available")


def _extract_function(source: str, name: str) -> str:
    """Return the named top-level function, matched by braces."""
    match = re.search(rf"^function\s+{re.escape(name)}\s*\{{", source, re.MULTILINE)
    assert match is not None, f"{DEV_UI.name} no longer defines {name}"
    start = match.start()
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{name} has an unbalanced brace")


def _run_advice(function_source: str, listening: bool, out_file: Path) -> list[str]:
    """Execute the real function for one outcome and read back its lines."""
    value = "$true" if listening else "$false"
    command = f"{function_source}\r\n$res = {FUNCTION_NAME} -NginxPort {NGINX_PORT} -NginxListening {value}\r\n[System.IO.File]::WriteAllLines('{out_file}', [string[]]$res)\r\n"
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert out_file.exists(), f"{FUNCTION_NAME} produced no file for listening={listening}; rc={completed.returncode} stderr={completed.stderr[-500:]}"
    return [line for line in out_file.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(scope="module")
def dev_ui_source() -> str:
    return DEV_UI.read_text(encoding="utf-8")


def test_dev_ui_declares_nginx_as_a_separate_command(dev_ui_source: str) -> None:
    """The script starts the frontend alone, so it must not assume nginx.

    Guards the premise of the rest of this module: if the launcher ever does
    start nginx itself, advertising 2026 unconditionally becomes correct and
    this suite - not a user - should be what notices.
    """
    assert "function Get-AlphaEntryPointAdvice" in dev_ui_source
    # No spawn of nginx anywhere in this launcher.
    assert not re.search(r"nginx\.exe|Start-Nginx|scripts[/\\]nginx", dev_ui_source)


def test_call_site_probes_the_port_before_advertising(dev_ui_source: str) -> None:
    """The 2026 sentence is reached only behind a live-listener probe."""
    assert "Get-NetTCPConnection -LocalPort 2026 -State Listen" in dev_ui_source
    # The advice call site must consume the probe, not a literal.
    assert re.search(
        r"Get-AlphaEntryPointAdvice\s+-NginxPort\s+2026\s+-NginxListening\s+\(\[bool\]\$nginxListener\)",
        dev_ui_source,
    ), "the entry-point advice must be gated on the listener probe"


def test_no_unconditional_2026_advertisement(dev_ui_source: str) -> None:
    """No bare Write-Host of the 2026 URL survives outside the helper."""
    offenders = [line for line in dev_ui_source.splitlines() if PROXY_SENTENCE_FRAGMENT in line and "Write-Host" in line]
    assert offenders == [], f"unconditional 2026 advertisement remains: {offenders}"


def test_advice_when_nginx_is_listening(tmp_path: Path) -> None:
    """With a listener, the unified entry point is what gets named."""
    function_source = _extract_function(DEV_UI.read_text(encoding="utf-8"), FUNCTION_NAME)
    lines = _run_advice(function_source, True, tmp_path / "advice.txt")
    assert len(lines) == 1
    assert f"127.0.0.1:{NGINX_PORT}" in lines[0]
    assert PROXY_SENTENCE_FRAGMENT in lines[0]


def test_advice_when_nginx_is_down_names_the_remedy(tmp_path: Path) -> None:
    """Without a listener, the dead URL is not printed - the fix is named."""
    function_source = _extract_function(DEV_UI.read_text(encoding="utf-8"), FUNCTION_NAME)
    lines = _run_advice(function_source, False, tmp_path / "advice.txt")
    joined = "\n".join(lines)
    # The URL that would be dead is never presented as an entry point.
    assert PROXY_SENTENCE_FRAGMENT not in joined
    # The port is still named, so the sentence is not a vague non-answer...
    assert str(NGINX_PORT) in joined
    # ...and the command that brings it up is what the user is told to run.
    assert "make nginx" in joined
