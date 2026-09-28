"""Regression coverage for #3758: macOS nginx argv rewriting broke make stop."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVE_SH = REPO_ROOT / "scripts" / "serve.sh"


def _extract_shell_function(name: str) -> str:
    text = SERVE_SH.read_text(encoding="utf-8")
    marker = f"{name}() {{"
    start = text.index(marker)
    depth = 0
    chunks: list[str] = []

    for line in text[start:].splitlines(keepends=True):
        chunks.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            return "".join(chunks)

    raise AssertionError(f"Could not extract shell function {name}")


def _is_repo_nginx_pid(
    *,
    command: str,
    args: str,
    repo_root: Path,
    alpha_pid: bool = False,
) -> bool:
    function = _extract_shell_function("_is_repo_nginx_pid")
    script = f"""
REPO_ROOT={shlex.quote(str(repo_root))}
ALPHA_ROOTS={shlex.quote(str(repo_root))}
FAKE_COMMAND={shlex.quote(command)}
FAKE_ARGS={shlex.quote(args)}
FAKE_ALPHA_PID={1 if alpha_pid else 0}

_is_alpha_pid() {{
    [ "$FAKE_ALPHA_PID" = "1" ]
}}

ps() {{
    case "$*" in
        *"-o comm="*) printf '%s\\n' "$FAKE_COMMAND" ;;
        *"-o args="*) printf '%s\\n' "$FAKE_ARGS" ;;
        *) return 1 ;;
    esac
}}

{function}

_is_repo_nginx_pid 12345
"""
    # On Windows, `shutil.which("bash")` finds the WSL launcher
    # (C:\Windows\System32\bash.exe) whenever WSL is installed, and that shim
    # fails with "execvpe(/bin/bash) failed: No such file or directory" when no
    # distro is present - so the test failed for a reason that had nothing to do
    # with serve.sh. The repository already ships the correct resolver for this
    # (scripts/run-with-git-bash.cmd, which is what the Makefile's
    # RUN_SHELL_SCRIPT uses on Windows); use it so the test exercises the same
    # bash the launcher actually runs under.
    #
    # The script goes in a file rather than an argument: the wrapper is a .cmd
    # that forwards with `%*`, which does not re-quote, so a multi-line script
    # containing spaces and shell metacharacters arrives split into many
    # arguments. Passing a single path avoids that entirely.
    if os.name == "nt":
        # REPO_ROOT, not the `repo_root` parameter: that one is the throwaway
        # fixture directory the script pretends to be running inside, so the real
        # wrapper is not under it.
        wrapper = REPO_ROOT / "scripts" / "run-with-git-bash.cmd"
        if not wrapper.is_file():
            pytest.skip(f"Git Bash wrapper not found at {wrapper}")
        script_file = repo_root.parent / "probe.sh"
        script_file.write_text(script, encoding="utf-8")
        command_line = [str(wrapper), str(script_file)]
    else:
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash is required to exercise serve.sh helpers")
        command_line = [bash, "-c", script]

    result = subprocess.run(command_line, check=False)
    return result.returncode == 0


def test_repo_nginx_pid_accepts_macos_rewritten_master_command(tmp_path):
    repo_root = tmp_path / "alpha"
    nginx_conf = repo_root / "docker" / "nginx" / "nginx.local.conf"

    assert _is_repo_nginx_pid(
        command=f"nginx: master process /opt/homebrew/bin/nginx -c {nginx_conf}",
        args=f"nginx: master process /opt/homebrew/bin/nginx -c {nginx_conf} -p {repo_root}",
        repo_root=repo_root,
    )


def test_repo_nginx_pid_accepts_macos_rewritten_worker_after_repo_check(tmp_path):
    repo_root = tmp_path / "alpha"

    assert _is_repo_nginx_pid(
        command="nginx: worker process",
        args="nginx: worker process",
        repo_root=repo_root,
        alpha_pid=True,
    )


@pytest.mark.parametrize(
    ("command", "args", "alpha_pid"),
    [
        ("nginx: worker process", "nginx: worker process", False),
        ("python", "python -m nginx /tmp/alpha/docker/nginx/nginx.local.conf", True),
    ],
)
def test_repo_nginx_pid_rejects_unowned_or_non_nginx_processes(
    tmp_path,
    command: str,
    args: str,
    alpha_pid: bool,
):
    assert not _is_repo_nginx_pid(
        command=command,
        args=args,
        repo_root=tmp_path / "alpha",
        alpha_pid=alpha_pid,
    )
