"""A started stack must become visible, and only once it is actually serving.

`make dev` brought the entire system up, printed "Alpha is running!", and
stopped. Nothing on screen proved the thing the user asked for worked, and the
only available signal was "is a TCP port bound" - which is a different claim. The
Next.js dev server binds :3000 and then needs roughly 50 seconds to compile `/`
(measured on this checkout), so opening a browser at port-bind time shows a
connection error at exactly the moment the user is looking for proof.

`scripts/open_alpha_ui.sh` is the shared piece: wait for HTTP 200, then open the
browser (and optionally the Electron desktop app), with a platform branch for
Git Bash - which has neither `xdg-open` nor `python3`, so the opener and the
probe both need a Windows path.

These tests pin the behaviour that matters, using the real script under a real
bash, and they deliberately do not require a browser to be installed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "open_alpha_ui.sh"
SERVE_SH = REPO_ROOT / "scripts" / "serve.sh"
START_SH = REPO_ROOT / "start.sh"
# The repository's own Git Bash wrapper. Bare `bash` on Windows resolves to the
# WSL shim, which fails with `execvpe(/bin/bash) failed` - the same reason
# `RUN_SHELL_SCRIPT` routes Makefile recipes through this wrapper (see
# scripts/AGENTS.md, "Shell Script Invocation Contract").
GIT_BASH_WRAPPER = REPO_ROOT / "scripts" / "run-with-git-bash.cmd"

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not GIT_BASH_WRAPPER.is_file(),
    reason="the launcher scripts are exercised through the Git Bash wrapper on Windows",
)


def _strip_comments(path: Path) -> str:
    """Drop `#` comments so prose naming a removed pattern is not that pattern.

    `start.sh` documents the blind `xdg-open` call it no longer makes, and an
    assertion that the string is absent would otherwise read the explanation as
    the code.
    """
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _run(args: list[str], env: dict[str, str] | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    # Never let a developer's real opt-out mask what the test is asserting.
    merged.pop("ALPHA_LAUNCH_DESKTOP", None)
    merged.update(env or {})
    return subprocess.run(
        [str(GIT_BASH_WRAPPER), str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
        timeout=timeout,
        check=False,
    )


def _bash_syntax_ok(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(GIT_BASH_WRAPPER), "-n", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_the_script_exists_and_is_valid_shell() -> None:
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    checked = _bash_syntax_ok(SCRIPT)
    assert checked.returncode == 0, checked.stderr


def test_both_launchers_stay_valid_shell_after_the_change() -> None:
    """A syntax error in a launcher only shows up at the user's next `make dev`."""
    for path in (SERVE_SH, START_SH):
        checked = _bash_syntax_ok(path)
        assert checked.returncode == 0, f"{path.name}: {checked.stderr}"


def test_a_missing_url_is_a_usage_error() -> None:
    assert _run([]).returncode == 2


def test_a_non_numeric_timeout_is_a_usage_error() -> None:
    # Silently treating "5s" as 5 would make the deadline a lie.
    result = _run(["http://127.0.0.1:9/", "5s"])
    assert result.returncode == 2
    assert "whole number of seconds" in result.stdout + result.stderr


def test_a_url_that_never_serves_is_reported_honestly() -> None:
    """Exit 1, and it must say the stack may still be starting.

    It must NOT print a success line: the whole point is that a timeout is a
    disclosure, not a green tick.
    """
    result = _run(["http://127.0.0.1:9/", "5"], env={"ALPHA_NO_BROWSER": "1"})
    assert result.returncode == 1, result.stdout
    combined = result.stdout + result.stderr
    assert "did not answer 200" in combined
    assert "is serving." not in combined


def test_a_non_200_response_is_not_mistaken_for_ready() -> None:
    """A bound port that answers 503 must not open a browser as if healthy.

    `wait-for-port.sh` only proves something is listening; the point of the HTTP
    gate is to catch a service that is up but not serving.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"degraded")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run([f"http://127.0.0.1:{port}/", "5"], env={"ALPHA_NO_BROWSER": "1"})
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 1, result.stdout
    assert "did not answer 200" in result.stdout


def test_a_serving_url_with_the_browser_suppressed_succeeds() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Alpha")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run([f"http://127.0.0.1:{port}/", "20"], env={"ALPHA_NO_BROWSER": "1"})
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stdout
    assert "is serving." in result.stdout
    assert "ALPHA_NO_BROWSER=1" in result.stdout, "the opt-out must be disclosed, not silent"


def test_serve_sh_opens_the_entry_point_that_is_actually_serving() -> None:
    """:2026 when nginx came up, the frontend port when it did not.

    Advertising :2026 while nginx is skipped would send the user (and the
    browser) to a port nothing is listening on.
    """
    source = SERVE_SH.read_text(encoding="utf-8")
    # `bash`, not `sh`: scripts/AGENTS.md requires a sibling repository script to
    # be invoked through an explicit interpreter, so the recipe keeps working in
    # a checkout that lost its executable bit. test_makefile_shell_script_invocation
    # enforces the same contract for the Makefile's own recipes.
    assert 'bash ./scripts/open_alpha_ui.sh "$UI_URL"' in source
    assert 'UI_URL="http://localhost:3000"' in source
    assert 'UI_URL="http://localhost:2026"' in source
    # The nginx-skipped branch must be the one that picks 3000.
    skipped_at = source.index('UI_URL="http://localhost:3000"')
    nginx_check = source.index('if [ "$NGINX_SKIPPED" = "true" ]; then', skipped_at - 400)
    assert nginx_check < skipped_at


def test_serve_sh_skips_the_probe_when_nothing_was_requested() -> None:
    """A headless launch must pay no probe cost."""
    source = SERVE_SH.read_text(encoding="utf-8")
    assert '[ "${ALPHA_NO_BROWSER:-0}" != "1" ] || [ "${ALPHA_LAUNCH_DESKTOP:-0}" = "1" ]' in source


def test_start_sh_shares_the_helper_instead_of_firing_xdg_open_blind() -> None:
    source = _strip_comments(START_SH)
    assert "open_alpha_ui.sh" in source
    # The old blind opener is gone: firing it on port-bind is the bug.
    assert "xdg-open" not in source
    # start.sh does not start nginx, so :2026 would never be listening.
    assert "localhost:${FRONTEND_PORT}" in source
    assert "localhost:2026" not in source.split("open_alpha_ui.sh")[1][:200]


def test_start_sh_also_skips_the_probe_when_nothing_was_requested() -> None:
    source = START_SH.read_text(encoding="utf-8")
    assert '[ "${ALPHA_NO_BROWSER:-0}" != "1" ] || [ "${ALPHA_LAUNCH_DESKTOP:-0}" = "1" ]' in source


def test_both_launchers_reuse_the_existing_readiness_knob() -> None:
    """One wait knob, so ALPHA_READY_WAIT_SECONDS still governs readiness."""
    for path in (SERVE_SH, START_SH):
        source = path.read_text(encoding="utf-8")
        assert "ALPHA_UI_READY_TIMEOUT:-${ALPHA_READY_WAIT_SECONDS:-300}" in source, path.name


def test_the_desktop_app_is_opt_in_not_silent() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ALPHA_LAUNCH_DESKTOP:-0" in source
    # Launching Electron without being asked would be a surprising second window.
    assert 'ALPHA_LAUNCH_DESKTOP:-0}" = "1"' in source
    assert "npm start" in source
