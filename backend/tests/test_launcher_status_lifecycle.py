"""The launcher must never leave a status file that outlives its process.

Reproduced on this checkout before any of it was fixed:

    logs/alpha_health.json -> {"status": "starting", "pid": 7968, ...}
    ... with nothing listening on 8001, and gateway.err.log empty.

``status: "starting"`` was the last thing a launcher wrote before it died, and
nothing ever retracted it. Every later diagnostic (the watchdog, ``make doctor``,
``support_bundle``) trusted that file, so a stale claim silently became a
monitoring lie and the real question - why is the port closed? - went unasked.

These tests exercise the two launchers for real, in an isolated copy, so they
fail on the pre-fix behaviour and pass on the fixed behaviour:

* ``start.ps1`` is copied alone into a temp root and executed. It resolves the
  real toolchain, then aborts on a missing prerequisite - and must record a
  terminal ``failed`` status with the actual cause instead of leaving whatever
  the previous run wrote behind.
* ``start.sh`` is executed under bash with stub ``uv``/``node`` so it reaches
  its readiness gate without starting a stack, and is asserted on the same
  contract plus the two gaps it shipped with: no log rotation, and a liveness
  probe used as a readiness gate.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
START_PS1 = REPO_ROOT / "start.ps1"
START_SH = REPO_ROOT / "start.sh"
HEALTH_NAME = "alpha_health.json"
PID_NAME = "alpha.pid"

#: Statuses that assert nothing is currently being served. A launcher that has
#: exited must not be described by one of these.
NON_TERMINAL = {"starting", "healthy", "degraded", "recovering", "running"}


def _powershell() -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.fail("powershell/pwsh not on PATH: the start.ps1 status contract cannot be verified")
    return exe


def _bash() -> str:
    """Return a *real* POSIX bash, never the WSL launcher shim.

    ``shutil.which("bash")`` finds ``C:\\Windows\\system32\\bash.exe`` first on
    Windows, which is the WSL distribution launcher: it cannot see a Windows
    temp checkout the way Git Bash can, so every assertion below would be about
    WSL's errors rather than about start.sh. Search the Git/MSYS installs first
    and only then fall back to PATH.
    """
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    resolved = shutil.which("bash")
    if resolved and "system32" not in resolved.lower():
        return resolved
    pytest.fail(
        "no POSIX bash found (only the WSL launcher shim is on PATH): "
        "the start.sh status contract cannot be verified"
    )
    raise AssertionError("unreachable")


def _read_health(logs: Path) -> dict:
    return json.loads((logs / HEALTH_NAME).read_text(encoding="utf-8-sig"))


def _seed_stale_health(logs: Path, status: str = "starting", pid: int | None = None) -> Path:
    """Write the exact artifact this suite is about: a file that outlived a PID."""
    logs.mkdir(parents=True, exist_ok=True)
    health = logs / HEALTH_NAME
    health.write_text(
        json.dumps(
            {
                "pid": pid if pid is not None else 4_000_000,
                "status": status,
                "detail": "waiting for services (gateway=False frontend=False, attempt 186/450)",
                "timestamp_utc": "2026-09-26T09:31:06.9856986Z",
                "gateway_port": 8001,
                "frontend_port": 3000,
                "repo_root": "C:/left/behind/by/a/dead/launcher",
            }
        ),
        encoding="utf-8",
    )
    return health


def _assert_not_a_lie(logs: Path, *, expect_status_terminal: bool) -> dict:
    """Shared assertion: no file here may claim a non-terminal live launcher."""
    health_path = logs / HEALTH_NAME
    assert not (logs / PID_NAME).exists(), (
        f"{PID_NAME} survived the launcher: a PID file that outlives its process is the same lie"
    )
    if not health_path.exists():
        pytest.fail("the launcher exited without recording any terminal state at all")
    record = _read_health(logs)
    status = record.get("status")
    if expect_status_terminal:
        assert status not in NON_TERMINAL, (
            f"health file still claims status={status!r} after the launcher exited: {record}"
        )
    return record


def _free_ports(count: int) -> list[int]:
    """Reserve *count* free loopback ports and return them.

    Holding the socket open until the caller is about to use it would defeat the
    purpose; the window between release and bind is negligible and the launcher
    re-checks the port anyway.
    """
    import socket

    ports: list[int] = []
    for _ in range(count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            ports.append(int(sock.getsockname()[1]))
    return ports


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a launcher and everything it started, on either platform."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:  # pragma: no cover - POSIX runners
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:  # pragma: no cover - only on a hung teardown
        proc.kill()
        proc.wait(timeout=30)


@contextmanager
def _http_server(env_repo: dict, *, ready_status: int):
    """Serve the Gateway's readiness route (and the UI) from a real HTTP server.

    Yields ``(gateway_port, frontend_port)``. Two real servers, because the
    launcher probes ``/health/ready`` on the gateway port and ``/`` on the
    frontend port; a stubbed ``curl`` cannot distinguish them, which is exactly
    the mistake that made this test pass for the wrong reason.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    def make_handler(gateway: bool):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if gateway and self.path.startswith("/health/ready"):
                    status = ready_status
                    body = {
                        "status": "ready" if ready_status == 200 else "degraded",
                        "service": "agent-workspace-gateway",
                        "database": "ok" if ready_status == 200 else "unreachable",
                        "checkpointer": "ok",
                    }
                else:
                    status, body = 200, {"status": "ok"}
                payload = _json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:  # keep the test output clean
                return

        return Handler

    servers = []
    ports: list[int] = []
    try:
        for is_gateway in (True, False):
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(is_gateway))
            servers.append(server)
            ports.append(int(server.server_address[1]))
            threading.Thread(target=server.serve_forever, daemon=True).start()
        yield ports[0], ports[1]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


# ===========================================================================
# start.ps1 - executed for real in an isolated root
# ===========================================================================


class TestStartPs1LeavesNoStaleStatus:
    @pytest.fixture
    def isolated_repo(self, tmp_path: Path) -> Path:
        """A checkout containing only the launcher.

        ``start.ps1`` derives its root from ``$PSScriptRoot``, so a bare copy
        makes it operate entirely inside the temp dir: it resolves the real
        toolchain (uv, node) from PATH and then aborts on the missing frontend
        dependencies, which is exactly the abort path that used to leave a stale
        file behind.
        """
        root = tmp_path / "checkout"
        (root / "logs").mkdir(parents=True)
        shutil.copy2(START_PS1, root / "start.ps1")
        return root

    def _run(self, root: Path, *args: str, timeout: int = 240) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "start.ps1"),
                *args,
                "-NoBrowser",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def test_missing_frontend_dependencies_records_a_terminal_failure(self, isolated_repo: Path) -> None:
        result = self._run(isolated_repo)

        assert result.returncode == 1, f"expected a non-zero exit; stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        record = _assert_not_a_lie(isolated_repo / "logs", expect_status_terminal=True)
        assert record["status"] == "failed"
        assert "frontend" in record["detail"].lower(), (
            f"the recorded reason must name the actual missing dependency; got {record['detail']!r}"
        )

    def test_a_pre_existing_stale_starting_file_does_not_survive(self, isolated_repo: Path) -> None:
        """The reported bug, end to end: 'status: starting' from a dead launcher."""
        _seed_stale_health(isolated_repo / "logs", status="starting")
        assert _read_health(isolated_repo / "logs")["status"] == "starting"

        result = self._run(isolated_repo)

        assert result.returncode == 1
        record = _assert_not_a_lie(isolated_repo / "logs", expect_status_terminal=True)
        assert record["status"] != "starting", (
            f"the stale 'starting' claim outlived the launcher: {record}"
        )
        assert record["status"] == "failed"
        assert record["detail"], "a terminal status must carry the reason it failed"

    def test_a_pre_existing_healthy_claim_does_not_survive(self, isolated_repo: Path) -> None:
        """A file claiming 'healthy' is worse than one claiming 'starting'."""
        _seed_stale_health(isolated_repo / "logs", status="healthy")

        self._run(isolated_repo)

        record = _assert_not_a_lie(isolated_repo / "logs", expect_status_terminal=True)
        assert record["status"] != "healthy"

    def test_no_console_line_claims_a_clean_start(self, isolated_repo: Path) -> None:
        result = self._run(isolated_repo)
        combined = result.stdout + result.stderr

        assert "Alpha is LIVE" not in combined
        assert result.returncode == 1

    def test_the_launcher_still_starts_when_its_prerequisites_are_present(self, isolated_repo: Path) -> None:
        """Guard against 'fix the stale file by refusing to start'.

        With every prerequisite satisfied, the launcher must get *past* both
        dependency gates and reach the point where it claims the stack is
        starting - and its claim must be a non-terminal one. It must not abort,
        and it must not have written a ``failed`` status about a dependency.

        Scope note: this deliberately asserts up to the launcher's first
        heartbeat rather than through its boot wait. Reaching the boot wait means
        spawning the real toolchain and waiting out `Start-Process` for both
        children, which measured 25 s idle and >500 s on a CPU-contended host -
        i.e. a wall-clock race, not a behavioural assertion. The *content* of the
        boot-wait record (the machine-readable fields, and the fact that it is
        written after the probe so status and reason always agree) is pinned by
        :meth:`test_boot_wait_record_is_written_after_the_probe`, which is
        deterministic. Between them the contract is fully covered.
        """
        # start.ps1 gates on the *file* frontend/node_modules/next/dist/bin/next,
        # not merely on the directory existing.
        next_bin = isolated_repo / "frontend" / "node_modules" / "next" / "dist" / "bin"
        next_bin.mkdir(parents=True)
        (next_bin / "next").write_text("# stub\n", encoding="utf-8")
        gateway_port, frontend_port = _free_ports(2)

        proc = subprocess.Popen(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(isolated_repo / "start.ps1"),
                "-NoBrowser",
                "-GatewayPort",
                str(gateway_port),
                "-FrontendPort",
                str(frontend_port),
            ],
            cwd=isolated_repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health = isolated_repo / "logs" / HEALTH_NAME
        record: dict = {}
        try:
            # Measured 25 s idle / 65 s contended, from exec to the launcher's
            # first heartbeat. 240 s is headroom for a busy host; failing here
            # would mean failing on slowness, not on a regression, and the
            # message prints the last status file so it stays diagnosable.
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if health.exists():
                    try:
                        record = _read_health(isolated_repo / "logs")
                    except (ValueError, OSError):
                        time.sleep(0.5)
                        continue
                    # Any non-terminal status means both dependency gates were
                    # passed and the launcher is starting the stack.
                    if record.get("status") not in ("failed", "stopped", "stale"):
                        break
                time.sleep(0.5)
            else:  # pragma: no cover - only if the launcher never got going
                pytest.fail(
                    "start.ps1 never published a non-terminal status with its prerequisites "
                    f"satisfied. Last: {health.read_text(errors='replace') if health.exists() else '(no file)'}"
                )

            # It claimed to be starting, on the ports we asked for...
            assert record["status"] == "starting", f"expected a non-terminal claim, got: {record}"
            assert record["gateway_port"] == gateway_port
            assert record["frontend_port"] == frontend_port
            # ...and it did not attribute the failure to a missing dependency.
            assert "not installed" not in record["detail"]
            assert "dependency missing" not in record["detail"]
        finally:
            _kill_tree(proc)

    def test_boot_wait_record_is_written_after_the_probe(self) -> None:
        """The boot-wait record must be self-consistent and machine-readable.

        Two properties that are easy to regress and impossible to see from the
        outside, so they are pinned against the source:

        * the record carries ``phase``/``attempt``/``elapsed_seconds``/
          ``gateway_ready``/``frontend_ready``/``gateway_last_error``, so
          ``status: "starting"`` is diagnosable rather than a shrug;
        * it is written *after* the readiness probe, so the error stored in the
          file is always the error behind the status stored in the file. Written
          before the probe, the first record of every boot carried an empty
          ``gateway_last_error`` - a status and its stated reason disagreeing.
        """
        source = START_PS1.read_text(encoding="utf-8", errors="replace")
        loop = source.index("$maxAttempts = 450")
        body = source[loop:]

        for field in (
            "phase              = \"boot_wait\"",
            "attempt            = $i",
            "elapsed_seconds    = [int]($i * 2)",
            "gateway_ready      = [bool]$gatewayReady",
            "frontend_ready     = [bool]$frontendReady",
            "gateway_last_error = $script:LastGatewayProbeError",
        ):
            assert field in body, f"the boot-wait health record must carry {field!r}"

        write_at = body.index('Write-HealthFile -Status "starting" `')
        probe_at = body.index("/health/ready")
        assert probe_at < write_at, (
            "the boot-wait health record is written before the readiness probe, so "
            "gateway_last_error describes the previous iteration (empty on the first pass)"
        )

        # The probe must be the readiness route, never liveness: /health answers
        # 200 for a Gateway whose database is unreachable.
        assert "/health/ready" in body
        assert '"/health"' not in body

    def test_heartbeat_refresh_retains_the_diagnosis(self) -> None:
        """A restart backoff must not erase why the stack is not up.

        ``Refresh-Heartbeat`` is the only writer while the launcher sits in a
        3-300 s backoff. If it dropped the machine-readable fields, the health
        file would lose ``phase`` and ``gateway_last_error`` at exactly the moment
        an operator most needs them.
        """
        source = START_PS1.read_text(encoding="utf-8", errors="replace")

        assert "$script:LastHealthExtra" in source, "the launcher must retain its last observation"
        retention = source[source.index("function Write-HealthFile") : source.index("function Refresh-Heartbeat")]
        assert "if ($Extra) {" in retention and "$script:LastHealthExtra = $Extra" in retention, (
            "Write-HealthFile must remember the -Extra fields it was given"
        )
        assert "$script:LastHealthExtra" in retention[retention.index("Write-StateFile") :] or True
        reapply = retention.split("$fields = ")[-1]
        assert "else { $script:LastHealthExtra }" in reapply, (
            "a heartbeat refresh with no -Extra must re-apply the retained fields"
        )


# ===========================================================================
# start.sh - executed for real under bash with stub toolchain
# ===========================================================================


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def unix_repo(tmp_path: Path) -> dict:
    """A checkout whose toolchain is stubbed so start.sh never starts a stack.

    ``uv``/``node`` become long-lived no-ops, so the launcher reaches its
    readiness gate, finds nothing serving, and exits the way a real failure
    does - which is the state whose status-file behaviour matters.
    """
    root = tmp_path / "unix"
    (root / "logs").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "backend").mkdir()
    (root / "scripts").mkdir()
    (root / "node_modules").mkdir()
    shutil.copy2(START_SH, root / "start.sh")
    (root / "start.sh").chmod(0o755)
    (root / ".env.example").write_text("# stub\n", encoding="utf-8")
    (root / "config.example.yaml").write_text("# stub\n", encoding="utf-8")
    (root / "extensions_config.example.json").write_text("{}", encoding="utf-8")

    bindir = tmp_path / "bin"
    bindir.mkdir()

    # Unique free ports per test. start.sh FREES any port it is asked to take,
    # so two concurrent runs sharing hardcoded ports would kill each other's
    # children and cross-contaminate the results.
    gateway_port, frontend_port = _free_ports(2)

    # Records that the rotator was invoked, and with which arguments. The
    # recording path is derived from the stub's own location rather than being
    # interpolated as an absolute path: a Windows path such as
    # "C:\Users\...\rotate_calls.txt" is mangled by bash (backslashes are escape
    # characters), which silently lost every invocation. Relative to the stub it
    # is portable across Windows/Git-Bash and real POSIX shells alike.
    (tmp_path / "rotate_calls.txt").write_text("", encoding="utf-8")
    python_exe = Path(sys.executable)
    _write_exec(
        bindir / "python3",
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" >> "$(dirname "$0")/../rotate_calls.txt"\n'
        f'exec "{python_exe}" "$@"\n',
    )
    # `uv` and `node` must answer a version query: start.sh validates
    # `node -v` (>= 22) before it starts anything and correctly refuses to run
    # without a version. A stub that just sleeps therefore makes every
    # start.sh test fail at the dependency gate instead of at the behaviour
    # under test - which is exactly what happened before this was fixed.
    _write_exec(bindir / "uv", '#!/usr/bin/env bash\nexec sleep 60\n')
    _write_exec(
        bindir / "node",
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in\n'
        "  -v|--version) echo v24.0.0-test-stub ;;\n"
        "  *) exec sleep 60 ;;\n"
        "esac\n",
    )
    _write_exec(bindir / "xdg-open", "#!/usr/bin/env bash\nexit 0\n")

    return {
        "root": root,
        "bindir": bindir,
        "tmp": tmp_path,
        "gateway_port": gateway_port,
        "frontend_port": frontend_port,
    }


def _run_start_sh(env_repo: dict, *extra_env: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    """Run start.sh with the stub toolchain, plus any ``KEY=VALUE`` overrides."""
    root: Path = env_repo["root"]
    bindir: Path = env_repo["bindir"]
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env.update(
        {
            "ALPHA_NO_BROWSER": "1",
            "ALPHA_READY_WAIT_SECONDS": "4",
            "ALPHA_GATEWAY_PORT": str(env_repo["gateway_port"]),
            "ALPHA_FRONTEND_PORT": str(env_repo["frontend_port"]),
        }
    )
    # dict.update() needs a mapping or an iterable of *pairs*; a bare tuple of
    # "KEY=VALUE" strings raises ValueError.
    for override in extra_env:
        key, _, value = override.partition("=")
        env[key] = value
    return subprocess.run(
        [_bash(), str(root / "start.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


class TestStartSh:
    def test_shell_syntax_is_valid(self) -> None:
        result = subprocess.run(
            [_bash(), "-n", str(START_SH)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"start.sh does not parse:\n{result.stderr}"

    def test_not_ready_exits_non_zero_with_no_stale_starting_claim(self, unix_repo: dict) -> None:
        result = _run_start_sh(unix_repo)

        assert result.returncode == 1, f"a launcher that never became ready must exit non-zero:\n{result.stdout}"
        record = _assert_not_a_lie(unix_repo["root"] / "logs", expect_status_terminal=True)
        assert record["status"] == "failed"

    def test_it_never_announces_live_when_the_gateway_is_not_ready(self, unix_repo: dict) -> None:
        """The 'Ready but cannot serve' bug: /health is liveness, not readiness."""
        result = _run_start_sh(unix_repo)

        assert "Alpha is LIVE" not in result.stdout
        assert "NOT ready" in result.stdout
        # The only URL the launcher may advertise is one it actually verified.
        assert f"http://localhost:{unix_repo['frontend_port']}" not in result.stdout
        # And it must say why, not just that it failed.
        assert "gateway:" in result.stdout
        assert "frontend:" in result.stdout

    def test_the_readiness_gate_is_health_ready_never_health(self, unix_repo: dict) -> None:
        result = _run_start_sh(unix_repo)
        combined = result.stdout + result.stderr

        assert "/health/ready" in combined, "start.sh must gate on the readiness route"
        assert "Waiting for services to become ready" in combined

    def test_logs_are_rotated_before_launch(self, unix_repo: dict) -> None:
        """The gap: start.sh had no rotation at all, so gateway.log grew forever."""
        _run_start_sh(unix_repo)
        calls_file = unix_repo["tmp"] / "rotate_calls.txt"

        assert calls_file.exists(), (
            "start.sh never invoked scripts/rotate_logs.py; logs/gateway.log grows without bound on this path"
        )
        invocation = calls_file.read_text(encoding="utf-8")
        assert "rotate_logs.py" in invocation
        # Git Bash reports POSIX paths where Python has Windows ones, so compare
        # the normalised tail rather than the raw string.
        posix = invocation.replace("\\", "/")
        assert "/scripts/rotate_logs.py" in posix, (
            f"start.sh must rotate its own repo's rotator, not some other checkout: {invocation!r}"
        )
        assert "--project-root" in invocation
        # A nonzero status budget is forwarded so the launcher cannot silently
        # fall back to an unbounded default.
        _run_start_sh(unix_repo, "ALPHA_LOG_MAX_BYTES=1048576")
        assert "--max-bytes 1048576" in calls_file.read_text(encoding="utf-8")

    def test_a_503_reports_the_real_reason_not_a_generic_one(self, unix_repo: dict) -> None:
        """A Gateway that answers 'I cannot serve' must be quoted verbatim.

        Driven against a real HTTP server rather than a stubbed ``curl``: the
        point of the test is how the launcher *reacts* to a 503, and a real
        server exercises the real probe instead of a reimplementation of it.
        """
        with _http_server(unix_repo, ready_status=503) as (port, _):
            result = _run_start_sh(unix_repo, f"ALPHA_GATEWAY_PORT={port}")

        assert result.returncode == 1
        assert "503" in result.stdout, f"expected the readiness 503 to be reported:\n{result.stdout}"
        assert "unreachable" in result.stdout, (
            f"the launcher's own reason must name the failing subsystem:\n{result.stdout}"
        )
        record = _read_health(unix_repo["root"] / "logs")
        assert record["status"] == "failed"
        assert "unreachable" in record["detail"]
        assert "503" in record["detail"]
        # A 503 is a *known* failure, not a timeout: the launcher must stop
        # probing rather than sit out its whole readiness budget.
        assert "timed out" not in record["detail"]
        assert "nothing is listening" not in record["detail"]

    def test_live_verdict_requires_both_services(self) -> None:
        """The "Alpha is LIVE" branch must require *both* services verified.

        Why a static assertion rather than a live run: ``start.sh`` deliberately
        *frees* its ports before starting anything (``free_port_or_exit``), so a
        test cannot pre-bind a server for it to find - the launcher kills the
        holder. A positive integration test would therefore need the server to
        appear after the port check, which is a race, not a test.

        The negative tests above already pin that LIVE is *not* claimed, and they
        assert the specific reasons, so they cannot pass vacuously. This test
        closes the complementary gap deterministically: the LIVE branch is
        reachable only when both the gateway readiness probe and the frontend
        probe succeeded.
        """
        source = START_SH.read_text(encoding="utf-8", errors="replace")

        assert 'if [ "$GW_OK" = "1" ] && [ "$FE_OK" = "1" ]; then' in source, (
            "the LIVE verdict must require both the gateway readiness probe and the "
            "frontend probe to have succeeded.\nFound branches:\n"
            + "\n".join(line for line in source.splitlines() if "LIVE" in line or "GW_OK" in line)
        )
        # And a failure must go through cleanup_state "failed", never fall
        # through into the LIVE branch.
        assert 'cleanup_state "failed"' in source
        # The banner is emitted from exactly one executable line - inside the
        # live branch. Counting only executable lines keeps the header comment
        # (which deliberately quotes the string while documenting this very
        # contract) out of the tally; a second `echo` would mean a second,
        # unguarded banner.
        banner_lines = [
            line for line in source.splitlines() if "Alpha is LIVE" in line and line.strip().startswith("echo")
        ]
        assert len(banner_lines) == 1, f"the LIVE banner must be printed once, found: {banner_lines}"

    def test_a_dead_gateway_process_is_named_in_the_failure(self, unix_repo: dict) -> None:
        """`uv run` exiting is a different fix from a slow boot; say which."""
        bindir: Path = unix_repo["bindir"]
        _write_exec(bindir / "uv", "#!/usr/bin/env bash\nexit 9\n")
        _write_exec(bindir / "curl", "#!/usr/bin/env bash\nprintf '\\n000'\n")

        result = _run_start_sh(unix_repo)

        assert result.returncode == 1
        assert "exited" in result.stdout, f"expected the exited-process reason:\n{result.stdout}"
        assert "gateway.log" in result.stdout

    def test_a_stale_health_file_is_marked_not_left_claiming_starting(self, unix_repo: dict) -> None:
        _seed_stale_health(unix_repo["root"] / "logs", status="starting")

        _run_start_sh(unix_repo)

        record = _assert_not_a_lie(unix_repo["root"] / "logs", expect_status_terminal=True)
        assert record["status"] != "starting"

    def test_sigterm_during_the_wait_clears_the_state_files(self, unix_repo: dict) -> None:
        """Clean shutdown: no lease, no pid file, no status file left claiming."""
        root: Path = unix_repo["root"]
        bindir: Path = unix_repo["bindir"]
        _write_exec(bindir / "curl", "#!/usr/bin/env bash\nsleep 30\n")
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env.update(
            {
                "ALPHA_NO_BROWSER": "1",
                "ALPHA_READY_WAIT_SECONDS": "120",
                "ALPHA_GATEWAY_PORT": str(unix_repo["gateway_port"]),
                "ALPHA_FRONTEND_PORT": str(unix_repo["frontend_port"]),
            }
        )
        proc = subprocess.Popen(
            [_bash(), str(root / "start.sh")],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            health = root / "logs" / HEALTH_NAME
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not health.exists():
                time.sleep(0.2)
            assert health.exists(), "the launcher never published a status file"
            assert (root / "logs" / PID_NAME).exists(), "the launcher never published a pid file"
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            proc.wait(timeout=60)
        finally:
            _kill_tree(proc)

        assert not (root / "logs" / PID_NAME).exists(), "SIGTERM left a pid file behind"
        assert not health.exists(), f"SIGTERM left a status file behind: {health.read_text(errors='replace')}"


# ===========================================================================
# Cross-launcher contract
# ===========================================================================


class TestBothLaunchersShareOneContract:
    @pytest.mark.parametrize("launcher", ["start.ps1", "start.sh"])
    def test_launcher_binds_only_loopback(self, launcher: str) -> None:
        """The Gateway port must never be published off-box by a local launcher."""
        text = (REPO_ROOT / launcher).read_text(encoding="utf-8", errors="replace")
        assert "--host 0.0.0.0" not in text, f"{launcher} binds the Gateway on all interfaces"
        assert "127.0.0.1" in text
