"""Regression anchor: every subprocess in these call sites has a real deadline.

Each of these paths runs an external command that can hang -- a wedged
container daemon, an index or proxy that never answers, a repository hook that
never returns, a credential probe that never speaks. Unbounded, each one parked
its caller forever with no way to report a failure. They are pinned here in two
ways:

* the call is *bounded* (a ``timeout`` is passed) and expiry becomes a clear,
  typed failure rather than a hang;
* where a real hanging process can be produced cheaply, the kill is proved
  externally -- a process id is published, and the assertion is that the process
  is actually gone. That matters on Windows, where killing only the direct child
  leaves a grandchild holding the inherited pipes, so an unbounded
  ``communicate()`` would still block.

The git tests use throwaway repositories created under ``tmp_path``; nothing here
touches the project's own repository.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.community.aio_sandbox.local_backend import LocalContainerBackend
from alpha.extensions import manager as extension_manager
from alpha.integrations import lark_cli
from alpha.projects import worktree_hook
from alpha.runtime.sentinel import commit as sentinel_commit

#: Wall clock a hanging test child sleeps. Every deadline under test is a small
#: fraction of this, so "terminated by the deadline" and "left running" cannot be
#: confused -- and a test that fails to terminate fails instead of hanging.
_HANG_SECONDS = 120


def _process_is_alive(pid: int) -> bool:
    """True while *pid* names a live process (never signals it, unlike ``os.kill``)."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            # WAIT_TIMEOUT (0x102) means the process is still running.
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_dead(pid: int, *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return True
        time.sleep(0.05)
    return not _process_is_alive(pid)


def _read_pid(pid_file: Path, *, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists():
            published = pid_file.read_text(encoding="utf-8").strip()
            if published:
                return int(published)
        time.sleep(0.05)
    raise AssertionError(f"{pid_file} never received a PID")


def _sleep_script(pid_file: Path) -> str:
    """A Python one-liner that publishes its PID, then sleeps far past any deadline."""
    return f"import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep({_HANG_SECONDS})"


# --------------------------------------------------------------------------- #
# docker run (sandbox create)
# --------------------------------------------------------------------------- #


def test_docker_run_is_bounded_and_reports_a_typed_failure(monkeypatch):
    """``_start_container`` bounds ``docker run`` and converts expiry to RuntimeError."""
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setattr(backend, "_runtime", "docker")

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        backend._start_container("sandbox-test", 18080)

    message = str(excinfo.value)
    assert f"did not complete the start within {LocalContainerBackend._START_TIMEOUT_SECONDS}s" in message
    # The message must not read like a port conflict, or the caller's retry loop
    # would burn through every candidate port instead of surfacing the failure.
    assert "port is already allocated" not in message
    assert "address already in use" not in message

    run_calls = [(cmd, kwargs) for cmd, kwargs in calls if cmd[:2] == ["docker", "run"]]
    assert run_calls, "the container was never started"
    _, kwargs = run_calls[0]
    assert kwargs["timeout"] == LocalContainerBackend._START_TIMEOUT_SECONDS, "docker run is still unbounded"


# --------------------------------------------------------------------------- #
# uv network operations
# --------------------------------------------------------------------------- #


@pytest.fixture()
def recorded_subprocess_run(monkeypatch):
    """Replace ``subprocess.run`` with one that records, and can time out on demand."""
    calls: list[tuple[list[str], dict[str, object]]] = []
    armed: dict[str, bool] = {"expire": True}

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        if armed["expire"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return SimpleNamespace(calls=calls, armed=armed)


def test_uv_sync_is_bounded_and_reports_a_typed_failure(recorded_subprocess_run, tmp_path):
    with pytest.raises(RuntimeError, match=f"uv sync did not complete within {extension_manager._UV_NETWORK_TIMEOUT_SECONDS:g}s"):
        extension_manager._run_uv(["uv", "sync", "--project", str(tmp_path)], tmp_path)

    argv, kwargs = recorded_subprocess_run.calls[0]
    assert argv[:2] == ["uv", "sync"]
    assert kwargs["timeout"] == extension_manager._UV_NETWORK_TIMEOUT_SECONDS
    assert kwargs["check"] is True, "the deadline must not turn a failed sync into a success"


def test_uv_version_probe_is_bounded(recorded_subprocess_run, tmp_path):
    with pytest.raises(RuntimeError, match=f"uv --version probe did not complete within {extension_manager._UV_PROBE_TIMEOUT_SECONDS:g}s"):
        extension_manager._require_supported_uv(tmp_path)

    argv, kwargs = recorded_subprocess_run.calls[0]
    assert argv == ["uv", "--version"]
    assert kwargs["timeout"] == extension_manager._UV_PROBE_TIMEOUT_SECONDS


def test_entry_point_probe_is_bounded(recorded_subprocess_run, tmp_path):
    with pytest.raises(RuntimeError, match="extension entry point probe for 'alpha-demo-extension' did not complete"):
        extension_manager._discover_installed_entry_point(tmp_path, "alpha-demo-extension")

    argv, kwargs = recorded_subprocess_run.calls[0]
    assert argv[0].endswith("python.exe") or argv[0].endswith("python")
    assert kwargs["timeout"] == extension_manager._UV_PROBE_TIMEOUT_SECONDS


def test_extras_detection_is_bounded(recorded_subprocess_run, tmp_path):
    project_root = tmp_path / "project"
    (project_root / "scripts").mkdir(parents=True)
    (project_root / "scripts" / "detect_uv_extras.py").write_text("print('')\n", encoding="utf-8")
    config_path = project_root / "config.yaml"
    config_path.write_text("plugins: []\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="optional-dependency flag detection did not complete"):
        extension_manager._detect_extra_flags(project_root, config_path)

    argv, kwargs = recorded_subprocess_run.calls[0]
    assert argv[1].endswith("detect_uv_extras.py")
    assert kwargs["timeout"] == extension_manager._UV_PROBE_TIMEOUT_SECONDS


# --------------------------------------------------------------------------- #
# worktree diff
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "sentinel@example.invalid")
    _git(repo, "config", "user.name", "Deadline Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _seed_commit(repo: Path) -> None:
    (repo / "file.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", "initial")


def _hanging_python_command(pid_file: Path) -> str:
    """A shell command line that publishes a PID and then hangs."""
    python = Path(sys.executable).as_posix()
    return f'"{python}" -c "{_sleep_script(pid_file)}" "{pid_file.as_posix()}"'


def _install_hanging_diff_driver(repo: Path, pid_file: Path) -> None:
    """Make ``git diff`` shell out to a command that never returns."""
    _git(repo, "config", "diff.external", _hanging_python_command(pid_file))


def _install_sleeping_hook(repo: Path, pid_file: Path) -> None:
    """Install a ``pre-commit`` hook that publishes its PID and then hangs."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{_hanging_python_command(pid_file)}\n", encoding="utf-8")
    hook.chmod(0o755)


def test_worktree_diff_deadline_kills_the_hanging_git_process(tmp_path):
    """A git invocation that hangs is killed -- process tree, not just the parent."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _seed_commit(repo)
    pid_file = tmp_path / "diff.pid"
    _install_hanging_diff_driver(repo, pid_file)
    (repo / "file.txt").write_text("second\n", encoding="utf-8")

    started = time.monotonic()
    with pytest.raises(worktree_hook.WorktreeDiffTimeout) as excinfo:
        worktree_hook._run_git_bounded(
            ["git", "-C", str(repo), "diff", "HEAD"],
            cwd=str(repo),
            timeout=3.0,
            description="worktree diff",
        )
    elapsed = time.monotonic() - started

    assert elapsed < 60.0, f"the bounded git call took {elapsed:.1f}s; the deadline did not terminate it"
    assert "worktree diff did not complete within 3s and was killed" in str(excinfo.value)
    diff_pid = _read_pid(pid_file)
    assert _wait_until_dead(diff_pid), f"external diff process {diff_pid} survived the git deadline"


def test_worktree_context_reports_a_failed_patch_instead_of_silent_success(tmp_path, monkeypatch):
    """A killed diff is recorded on the result, so 'no patch' != 'no changes'."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _seed_commit(repo)
    pid_file = tmp_path / "diff.pid"
    _install_hanging_diff_driver(repo, pid_file)
    (repo / "file.txt").write_text("second\n", encoding="utf-8")
    monkeypatch.setattr(worktree_hook, "_DIFF_TIMEOUT_SECONDS", 3.0)

    context = worktree_hook.WorktreeTaskContext(
        repo_path=repo,
        agent="coder",
        project_id="proj",
        task_id="task-1",
        cleanup_on_exit=False,
    )
    context.instance = SimpleNamespace(path=repo)

    context.__exit__(None, None, None)

    assert context.result is not None
    assert context.result.patch_generated is False
    assert context.result.patch_error == "worktree diff did not complete within 3s and was killed"
    diff_pid = _read_pid(pid_file)
    assert _wait_until_dead(diff_pid), f"external diff process {diff_pid} survived the diff deadline"


def test_worktree_diff_still_returns_output_without_a_hanging_driver(tmp_path):
    """The bounded runner keeps the ordinary diff path intact."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _seed_commit(repo)
    (repo / "file.txt").write_text("second\n", encoding="utf-8")

    diff = worktree_hook._run_git_bounded(["git", "-C", str(repo), "diff", "HEAD"], cwd=str(repo), timeout=60.0, description="worktree diff")

    assert "+second" in diff


def test_worktree_task_context_still_generates_a_patch(tmp_path):
    """The bounded diff keeps the ordinary worktree/patch lifecycle intact."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _seed_commit(repo)

    context = worktree_hook.WorktreeTaskContext(
        repo_path=repo,
        agent="coder",
        project_id="proj",
        task_id="task-1",
        cleanup_on_exit=False,
        generate_patch_on_exit=True,
    )
    with context as instance:
        (instance.path / "added.py").write_text("x = 1\n", encoding="utf-8")

    assert context.result is not None
    assert context.result.success is True
    assert context.result.patch_generated is True
    assert "+x = 1" in context.result.patch_content
    assert context.result.patch_error is None
    assert (instance.path / "task-1.patch").read_text(encoding="utf-8") == context.result.patch_content


# --------------------------------------------------------------------------- #
# Sentinel commit
# --------------------------------------------------------------------------- #


def test_sentinel_commit_reports_a_killed_hook_instead_of_hanging(tmp_path, monkeypatch):
    """A hanging commit hook becomes a failed CommitResult, with the hook killed."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    pid_file = tmp_path / "hook.pid"
    _install_sleeping_hook(repo, pid_file)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    monkeypatch.setattr(sentinel_commit, "_COMMIT_TIMEOUT_SECONDS", 3.0)

    committer = sentinel_commit.Committer(repo)
    started = time.monotonic()
    result = committer.commit(["file.txt"], "should never land")
    elapsed = time.monotonic() - started

    assert elapsed < 60.0, f"the Sentinel commit took {elapsed:.1f}s; the deadline did not terminate it"
    assert result.ok is False
    assert "did not complete within 3s and was killed" in (result.error or "")
    assert result.staged == ["file.txt"]
    assert result.sha is None
    hook_pid = _read_pid(pid_file)
    assert _wait_until_dead(hook_pid), f"hook process {hook_pid} survived the commit deadline"


def test_sentinel_commit_still_works_without_a_hook(tmp_path):
    """The bounded runner keeps the ordinary commit path intact."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")

    result = sentinel_commit.Committer(repo).commit(["file.txt"], "ordinary commit")

    assert result.ok is True
    assert result.sha and len(result.sha) == 40
    assert result.staged == ["file.txt"]
    assert result.error is None


# --------------------------------------------------------------------------- #
# Lark CLI SID probe
# --------------------------------------------------------------------------- #


def test_whoami_sid_probe_is_bounded(monkeypatch):
    """The Windows SID probe cannot hang, and expiry is a typed RuntimeError."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(lark_cli.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="whoami did not answer within"):
        lark_cli._resolve_current_user_sid()

    argv, kwargs = calls[0]
    assert argv == ["whoami", "/user", "/fo", "csv", "/nh"]
    assert kwargs["timeout"] == lark_cli._WHOAMI_TIMEOUT_SECONDS
