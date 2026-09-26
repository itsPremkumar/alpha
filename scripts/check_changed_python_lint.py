#!/usr/bin/env python3
"""Run Ruff only on the Python files a revision actually changed.

The repository has a disclosed, pre-existing Ruff debt backlog, so a full-tree
``ruff check`` is a measurement, not a pass/fail signal.  This gate is
intentionally incremental: a pull request or push must not introduce a new lint
or format finding in a file it owns, while the separate non-gating debt report
makes the remaining backlog visible without pretending it is clean.

The file list comes from Git's trusted base/head comparison.  Deleted files are
not linted because there is no content to check.  No files are written.

ONE RULE PER FILE, NOT ONE RULE PER CALLER
------------------------------------------
Ruff finds a configuration by walking up from each file, and falls back to the
configuration discovered from the *current working directory* when a file's
directory tree has none.  Only ``backend/`` has a ``ruff.toml`` here, so an
unconfigured file such as ``scripts/check_changed_python_lint.py`` is checked at
line-length 88 when ruff runs from the repository root and at line-length 240
when it runs from ``backend/``.  A gate whose verdict depends on the directory
someone happened to launch it from is not a gate, so the changed files are
partitioned by configuration scope: files under the backend root are checked
with the project configuration, and everything else is checked with
``--isolated``, which is ruff's documented default configuration and exactly
what a contributor gets at the repository root.  The two verdicts are reported
separately and either one fails the gate.

EVERY SUBPROCESS IS BOUNDED
---------------------------
A gate that hangs is not a gate, so each child process runs under an explicit
timeout (``--git-timeout``, ``--ruff-timeout``).  On expiry the whole process
tree is killed and the gate exits 1 with a disclosed message; it never waits for
the platform default.  A child that cannot even start fails the gate the same
way.  CI additionally caps the job with ``timeout-minutes``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_SECONDS = 120
RUFF_TIMEOUT_SECONDS = 300


class GateError(RuntimeError):
    """A gate-level failure that must fail closed rather than pass silently."""


class CommandTimeout(GateError):
    """A child process outlived its budget and was terminated."""


@dataclass(frozen=True)
class CommandResult:
    """The bounded result of one child process."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--backend-root", type=Path)
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument(
        "--git-timeout",
        type=float,
        default=GIT_TIMEOUT_SECONDS,
        help=(
            "Seconds each git invocation may run before it is killed and the "
            f"gate fails (default: {GIT_TIMEOUT_SECONDS})."
        ),
    )
    parser.add_argument(
        "--ruff-timeout",
        type=float,
        default=RUFF_TIMEOUT_SECONDS,
        help=(
            "Seconds each ruff invocation may run before it is killed and the "
            f"gate fails (default: {RUFF_TIMEOUT_SECONDS})."
        ),
    )
    return parser


def _display_command(argv: Sequence[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(list(argv))
    return shlex.join(str(item) for item in argv)


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate a child and everything it spawned; never leave an orphan.

    ``taskkill /T`` walks the tree before killing, so it can itself return a
    non-zero code when a child exits mid-walk.  A non-zero code therefore falls
    back to killing the direct child rather than being treated as success.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is None or result.returncode != 0:
            process.kill()
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError, PermissionError):
            process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - kill(9) did not land
        pass


def run_command(
    argv: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    timeout: float,
) -> CommandResult:
    """Run a child process under a hard timeout and fail closed on expiry."""
    spawn: dict[str, object] = {}
    if os.name == "nt":
        spawn["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn["start_new_session"] = True
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is built, never shell=True
            [str(item) for item in argv],
            cwd=None if cwd is None else str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **spawn,  # type: ignore[arg-type]
        )
    except OSError as exc:
        raise GateError(
            f"command could not start: {_display_command(argv)}: {exc}"
        ) from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - pipes never closed
            pass
        raise CommandTimeout(
            f"command exceeded its {timeout:g}s budget and was killed: "
            f"{_display_command(argv)}"
        ) from None
    return CommandResult(
        argv=tuple(str(item) for item in argv),
        returncode=process.returncode,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )


def _git(repo_root: Path, args: Sequence[str], *, timeout: float) -> bytes:
    result = run_command(["git", *args], cwd=repo_root, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _changed_python_paths(
    repo_root: Path,
    base_ref: str | None,
    head_ref: str | None,
    before: str | None,
    after: str | None,
    *,
    timeout: float,
) -> list[Path]:
    if base_ref and head_ref:
        if set(base_ref) == {"0"}:
            raw = _git(
                repo_root,
                ["ls-tree", "-r", "--name-only", "-z", head_ref],
                timeout=timeout,
            )
        else:
            raw = _git(
                repo_root,
                [
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    "-z",
                    f"{base_ref}...{head_ref}",
                    "--",
                ],
                timeout=timeout,
            )
    elif before and after:
        if set(before) == {"0"}:
            raw = _git(
                repo_root,
                ["ls-tree", "-r", "--name-only", "-z", after],
                timeout=timeout,
            )
        else:
            raw = _git(
                repo_root,
                [
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    "-z",
                    f"{before}..{after}",
                    "--",
                ],
                timeout=timeout,
            )
    else:
        raw = _git(
            repo_root,
            ["diff", "--name-only", "--diff-filter=ACMR", "-z", "HEAD", "--"],
            timeout=timeout,
        )
        raw += _git(
            repo_root,
            [
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "*.py",
            ],
            timeout=timeout,
        )
    paths: list[Path] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        relative = Path(item.decode("utf-8", errors="surrogateescape"))
        if relative.suffix == ".py" and (repo_root / relative).is_file():
            paths.append(relative)
    return sorted(set(paths), key=lambda path: path.as_posix())


def _config_scopes(
    repo_root: Path, backend_root: Path, paths: Sequence[Path]
) -> tuple[list[Path], list[Path]]:
    """Split changed files into (project-configured, ruff-default) groups.

    Only files under the backend root have a ``ruff.toml`` above them.  The
    other group is checked with ``--isolated`` so its verdict cannot depend on
    the working directory the gate was launched from.
    """
    try:
        scope = backend_root.relative_to(repo_root).parts
    except ValueError:
        scope = ("backend",)
    inside: list[Path] = []
    outside: list[Path] = []
    for path in paths:
        (inside if path.parts[: len(scope)] == scope else outside).append(path)
    return inside, outside


def _ruff_command(
    backend_root: Path,
    subcommand: str,
    paths: Sequence[Path],
    *,
    isolated: bool,
) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        command = [uv, "run", "--no-sync", "ruff", subcommand]
    else:
        command = [sys.executable, "-m", "ruff", subcommand]
    if isolated:
        command.append("--isolated")
    command.extend(str(path) for path in paths)
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if bool(args.base_ref) != bool(args.head_ref):
        print(
            "ERROR --base-ref and --head-ref must be provided together", file=sys.stderr
        )
        return 2
    if bool(args.before) != bool(args.after):
        print("ERROR --before and --after must be provided together", file=sys.stderr)
        return 2
    if args.base_ref and args.before:
        print(
            "ERROR choose either --base-ref/--head-ref or --before/--after",
            file=sys.stderr,
        )
        return 2
    if args.git_timeout <= 0 or args.ruff_timeout <= 0:
        print(
            "ERROR --git-timeout and --ruff-timeout must be positive", file=sys.stderr
        )
        return 2

    repo_root = args.repo_root.resolve()
    backend_root = (args.backend_root or repo_root / "backend").resolve()
    try:
        paths = _changed_python_paths(
            repo_root,
            args.base_ref,
            args.head_ref,
            args.before,
            args.after,
            timeout=args.git_timeout,
        )
    except GateError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    print(f"changed Python files: {len(paths)}")
    if not paths:
        print("incremental ruff gate: 0 (no changed Python files)")
        return 0
    for path in paths:
        print(f"  {path.as_posix()}")

    project_paths, default_paths = _config_scopes(repo_root, backend_root, paths)
    project_resolved = [(repo_root / path).resolve() for path in project_paths]
    default_resolved = [(repo_root / path).resolve() for path in default_paths]
    if default_paths:
        print(
            f"unconfigured Python files (checked with ruff defaults): {len(default_paths)}"
        )
        for path in default_paths:
            print(f"  {path.as_posix()}")
    invocations = (
        ("check", project_resolved, False),
        ("check", default_resolved, True),
        ("format", ["--check", *project_resolved], False),
        ("format", ["--check", *default_resolved], True),
    )
    for subcommand, targets, isolated in invocations:
        if not targets:
            continue
        command = _ruff_command(backend_root, subcommand, targets, isolated=isolated)
        print("running:", _display_command(command))
        try:
            result = run_command(
                command,
                cwd=backend_root,
                timeout=args.ruff_timeout,
            )
        except GateError as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1
        if result.stdout:
            sys.stdout.write(result.stdout.decode("utf-8", errors="replace"))
        if result.stderr:
            sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        sys.stderr.flush()
        if result.returncode != 0:
            scope = "ruff defaults" if isolated else "project config"
            print(
                f"incremental ruff gate: FAILED (exit {result.returncode}, {scope})",
                file=sys.stderr,
            )
            return result.returncode
    print("incremental ruff gate: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
