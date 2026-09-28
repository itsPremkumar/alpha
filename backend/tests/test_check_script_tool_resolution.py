"""`make check` must see the nginx the installer provisioned.

`install.bat` unpacks a pinned nginx into `.tools/` (installer/pins.json ->
scripts/toolchain.ps1::Install-AlphaNginx) so a Windows user needs neither WSL,
an admin install, nor Docker to run the local stack. `scripts/check.py` only
asked `shutil.which("nginx")`, so on a machine where the installer had just put
nginx on disk it reported:

    Checking nginx...
      FAIL nginx not found
        Windows: use WSL for local mode or use Docker mode

...advising WSL or Docker for a binary sitting in the repository, and failing the
build. Every `make` target runs `./scripts/check.py` first, so `make dev` could
not start at all. The provisioning worked and the preflight could not see it.

These tests pin the resolution order (project-local before PATH) and the honest
provenance label, so the check and the launchers cannot disagree about what is
installed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_PY = REPO_ROOT / "scripts" / "check.py"


def _load_check_module():
    spec = importlib.util.spec_from_file_location("alpha_check_script", CHECK_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_check_module()


def test_the_project_local_nginx_is_preferred(tmp_path: Path, monkeypatch) -> None:
    """A provisioned nginx wins, and is reported as such rather than silently."""
    tools = tmp_path / ".tools" / "nginx"
    tools.mkdir(parents=True)
    binary = tools / check.NGINX_BINARY_NAME
    binary.write_bytes(b"not a real binary")
    monkeypatch.setattr(check, "PROJECT_TOOLS_DIR", tmp_path / ".tools")
    # A PATH nginx must lose to the project one.
    monkeypatch.setattr(check.shutil, "which", lambda _name: "/usr/bin/nginx")

    assert check.resolve_nginx() == str(binary)


def test_falls_back_to_path_when_nothing_is_provisioned(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(check, "PROJECT_TOOLS_DIR", tmp_path / "empty")
    monkeypatch.setattr(check.shutil, "which", lambda _name: "/usr/bin/nginx")

    assert check.resolve_nginx() == "/usr/bin/nginx"


def test_returns_none_when_neither_source_has_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(check, "PROJECT_TOOLS_DIR", tmp_path / "empty")
    monkeypatch.setattr(check.shutil, "which", lambda _name: None)

    assert check.resolve_nginx() is None


def test_a_bare_nginx_file_without_the_executable_name_is_not_mistaken_for_one(tmp_path: Path, monkeypatch) -> None:
    """Only the exact binary name counts.

    `.tools/` is a general tool directory; picking up an unrelated `nginx.conf`
    or a nested build artifact would report an nginx that cannot run.
    """
    tools = tmp_path / ".tools" / "nginx"
    tools.mkdir(parents=True)
    (tools / "nginx.conf").write_text("worker_processes 1;", encoding="utf-8")
    monkeypatch.setattr(check, "PROJECT_TOOLS_DIR", tmp_path / ".tools")
    monkeypatch.setattr(check.shutil, "which", lambda _name: None)

    assert check.resolve_nginx() is None


def _strip_comments(source: str) -> str:
    """Drop `#` comments so prose about a pattern is not read as the pattern.

    These tests assert on the absence of a string, and the file deliberately
    documents the exact strings it removed. Without this, the documentation of
    `text=True` would be the thing that trips the `text=True` check.
    """
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split("  #", 1)[0])
    return "\n".join(lines)


def test_run_command_decodes_utf8_rather_than_the_locale() -> None:
    """The preflight runs version banners; a locale decode must not fail it.

    `text=True` alone decodes with cp1252 on Windows, which cannot represent most
    bytes these tools emit, and a failed decode made the caller report a tool as
    broken rather than as having an unprintable banner.
    """
    source = _strip_comments(CHECK_PY.read_text(encoding="utf-8"))
    body = source[source.index("def run_command(") : source.index("def resolve_nginx(")]
    assert 'encoding="utf-8"' in body, body
    assert 'errors="replace"' in body, body
    assert "text=True" not in body, body


def test_the_failure_advice_points_at_the_installer_not_at_wsl() -> None:
    """The old message told Windows users to use WSL or Docker for a local file."""
    source = _strip_comments(CHECK_PY.read_text(encoding="utf-8"))
    assert "use WSL for local mode" not in source
    assert "install.bat" in source
