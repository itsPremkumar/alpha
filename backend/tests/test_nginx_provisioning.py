"""The :2026 entry point must be installable, and optional when it is not.

nginx is the published entry point in the documented topology, but it is a real
third-party runtime rather than a Python or Node package, so no dependency
install step could ever provide it. On a machine without nginx the launcher
discovered that by starting it, waiting out a 10-second port timeout, and then
calling the shared cleanup path - which tore down the Gateway and the frontend
that had just come up. The user was left with nothing running and an error
naming a binary they had never asked for.

These tests pin the three things that make that class of failure impossible to
reintroduce:

1. nginx has a pin, so a version is decided in one place
   (``installer/pins.json``) rather than guessed in a downloader.
2. the installer can provision it, and provisioning failure is a warning.
3. the launcher treats it as optional, so a missing nginx degrades to the two
   direct ports instead of taking the whole stack down.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLCHAIN = REPO / "scripts" / "toolchain.ps1"
INSTALLER = REPO / "install.ps1"
SERVE = REPO / "scripts" / "serve.sh"
PINS = REPO / "installer" / "pins.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_nginx_has_a_pin_in_the_single_source_of_truth() -> None:
    """A version the installer downloads must be pinned, like uv and Node."""
    pins = json.loads(read(PINS))

    assert "nginx" in pins, f"installer/pins.json must pin nginx; found {sorted(pins)}"
    entry = pins["nginx"]
    assert entry.get("version"), "the nginx pin must name a version"
    assert entry.get("windowsDownloadBase", "").startswith("https://"), "the nginx pin must name its download base"


def test_the_nginx_pin_is_a_minor_series_not_a_three_part_version() -> None:
    """nginx names downloads ``nginx-1.27.5.zip``, so a three-part pin never matches.

    The download index offers every patch in a series and never a ``1.27.x``
    pattern, so pinning ``1.28.0`` produced "No nginx 1.28.0 Windows build
    found" on a machine where nginx existed and was downloadable.
    """
    version = json.loads(read(PINS))["nginx"]["version"]

    assert re.fullmatch(r"\d+\.\d+", str(version)), f"the nginx pin must be a minor series like '1.27' because that is how nginx names its Windows zips (nginx-1.27.5.zip); got {version!r}"


def test_toolchain_can_resolve_and_install_nginx() -> None:
    """The toolchain module owns nginx, the same way it owns uv and Node."""
    text = read(TOOLCHAIN)

    assert re.search(r"^function Resolve-AlphaNginx\b", text, re.MULTILINE), "toolchain.ps1 must define Resolve-AlphaNginx"
    assert re.search(r"^function Install-AlphaNginx\b", text, re.MULTILINE), "toolchain.ps1 must define Install-AlphaNginx"
    assert re.search(r"^function Get-AlphaNginxWellKnownDirs\b", text, re.MULTILINE), "toolchain.ps1 must enumerate conventional nginx directories"


def test_nginx_install_failure_is_a_warning_not_an_error() -> None:
    """A machine that cannot download nginx must still get a working Alpha.

    nginx is the only optional piece of the documented topology: the Gateway
    (:8001) and the frontend (:3000) each serve their own port, so losing it
    costs the unified entry point and nothing else.
    """
    text = read(TOOLCHAIN)

    install_body = text.split("function Install-AlphaNginx", 1)[1]
    # Every early return in the installer is a warning, so a failure surfaces as
    # a message rather than a terminating error under $ErrorActionPreference=Stop.
    early_returns = re.findall(r"Write-Warning[^\n]*\n\s*return \$?null", install_body)
    assert len(early_returns) >= 3, "Install-AlphaNginx must report every failure path with Write-Warning before returning null, because a throw here would abort install.ps1 entirely"
    assert "throw" not in install_body, "Install-AlphaNginx must not throw; nginx is optional and a throw would abort the whole install"


def test_the_installer_attempts_nginx_and_survives_its_absence() -> None:
    """install.ps1 must try to provision nginx and continue if that fails."""
    text = read(INSTALLER)

    assert "Install-AlphaNginx" in text, "install.ps1 must provision nginx"
    assert "Resolve-AlphaNginx" in text, "install.ps1 must reuse the toolchain resolver rather than searching for nginx itself"

    step = text.split("Install-AlphaNginx", 1)[1][:1500]
    assert "Write-Warning" in step or "WARN" in step, "a failed nginx provision must be reported as a warning"
    assert "exit 1" not in step, "a failed nginx provision must not abort the installer; Alpha runs without :2026"


def test_the_launcher_treats_nginx_as_optional() -> None:
    """A missing nginx must not tear down the Gateway and the frontend.

    This is the regression that cost a working stack: run_service() called the
    shared cleanup path on any startup failure, so one absent third-party
    binary stopped every service.
    """
    text = read(SERVE)

    run_service = text.split("run_service()", 1)[1].split("\n}", 1)[0]
    assert "optional" in run_service, "run_service must support an optional service that does not trigger cleanup"
    assert re.search(r'\[ "\$\{5:-\}" = "optional" \]', run_service), "run_service must branch on a 5th `optional` argument so a failing optional service returns instead of calling cleanup"

    # cleanup must still exist for the services that are NOT optional, or the
    # branch above would be a way to silently ignore a broken Gateway.
    assert "cleanup 1" in run_service, "a non-optional service failure must still call cleanup; otherwise a broken Gateway would be reported as a successful start"


def test_the_launcher_resolves_the_project_local_nginx_first() -> None:
    """The pinned copy must win over a stale system-wide nginx.

    Same rule as Resolve-AlphaTool: a stale system binary shadowing the pinned
    one makes the project-local guarantee cosmetic.
    """
    text = read(SERVE)

    assert re.search(r"\$REPO_ROOT/\.tools/nginx", text), "serve.sh must look for the project-local nginx under .tools/"
    assert "command -v" in text, "serve.sh must still fall back to PATH when no project-local copy exists"


def test_the_launcher_does_not_advertise_a_port_it_did_not_start() -> None:
    """The ready banner must name the ports that are actually listening.

    It previously printed http://localhost:2026 unconditionally, including on
    the run where nginx was absent, so the one address it named was the one that
    did not work.
    """
    text = read(SERVE)

    assert "NGINX_SKIPPED" in text, "serve.sh must track whether nginx was skipped"
    banner = text.split("# ── Ready ─", 1)[1]
    assert "localhost:2026" in banner and "NGINX_SKIPPED" in banner, "the :2026 address must be gated on nginx actually having started"
    assert "localhost:3000" in banner, "the banner must name the frontend port, which works with or without nginx"


@pytest.mark.parametrize("path", [TOOLCHAIN, INSTALLER], ids=lambda p: p.name)
def test_the_launcher_scripts_contain_no_user_profile_literal(path: Path) -> None:
    """Tool locations stay machine-independent.

    Matches the existing test_portable_paths rule: a launcher must derive its
    paths from $PSScriptRoot, never from a literal that names one machine.
    """
    for line in read(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "http" in stripped:
            continue
        if re.search(r"[A-Za-z]:\\Users\\", stripped):
            raise AssertionError(f"{path.name} hardcodes a user profile: {stripped[:120]}")
