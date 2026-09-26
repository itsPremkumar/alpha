#!/usr/bin/env python3
"""Run Ruff only on the Python files a revision actually changed.

The repository has a disclosed, pre-existing Ruff debt backlog, so a full-tree
``ruff check`` is a measurement, not a pass/fail signal.  This gate is
intentionally incremental: a pull request or push must not introduce a new lint
or format finding in a file it owns, while the separate non-gating debt report
makes the remaining backlog visible without pretending it is clean.

The file list comes from Git's trusted base/head comparison.  Deleted files are
not linted because there is no content to check.  No files are written.

ONE RULE SET FOR THE WHOLE REPOSITORY
-------------------------------------
The gate used to split the changed files in two and check one half with
``--isolated``, which is ruff's default configuration (``E4``, ``E7``, ``E9``,
``F``).  That existed because ``backend/ruff.toml`` used to be the only ruff
configuration in the tree, so a non-backend file's verdict depended on the
directory ruff happened to be launched from, and pinning it to the documented
defaults at least made that verdict reproducible.  It was reproducibility bought
with blindness: under those defaults E501, I001 and the whole ``UP`` family are
not selected at all, so 108 findings in first-party non-backend code were
measured by nobody - this gate could not see them and the debt report only
walked ``backend/``.

There is now a policy at the repository root that covers the whole tree, and
``backend/ruff.toml`` extends it, so every file is checked with the same rules
(E, F, I, UP at line-length 240) regardless of where it lives or which
directory the gate runs from.  ``--isolated`` is therefore gone, and with it
the blind spot.

SAME SCOPE AS THE DEBT REPORT
-----------------------------
The debt report in ``.github/workflows/lint-check.yml`` imports
``scripts/ruff_scope.py`` and measures the same file set, so the two cannot
drift apart.  A gate and a debt report that cover different files is how findings
stay invisible, so there is one definition and both callers use it.  Files that
are deliberately not linted are named there, each with a written reason, and
this gate prints any such file it is asked to skip rather than skipping it
quietly.

THE POLICY IS VERIFIED, NOT ASSUMED
-----------------------------------
Before it trusts a verdict, the gate asks ruff which settings a representative
of each configuration scope actually resolved, and fails if a scope resolved to
no configuration at all (ruff then silently falls back to its defaults, which is
the original bug) or resolved to something other than the repository policy.
It also refuses to pass while the resolved policy carries a hidden ``exclude`` or
a ``per-file-ignores`` table: a finding that is suppressed rather than fixed
must not be able to hide behind configuration.

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
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ruff_scope  # noqa: E402 - the shared file set, deliberately next door

ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_SECONDS = 120
RUFF_TIMEOUT_SECONDS = 300

# ruff --show-settings prints one `key = value` line per resolved setting.  These
# are the ones that prove a scope is on the repository policy rather than on
# ruff's defaults: 88 is the default line length, and an empty exclude /
# per_file_ignores is what an unweakened policy looks like.
SETTINGS_PATH_RE = re.compile(r'^Settings path: "(?P<path>.+)"$', re.MULTILINE)
SETTING_RE = re.compile(r"^(?P<key>[a-z_]+\.[a-z_]+) = (?P<value>.*)$", re.MULTILINE)


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
    parser.add_argument(
        "--backend-root",
        type=Path,
        help=(
            "Directory whose locked environment provides ruff (default: "
            "REPO/backend). This is NOT a lint scope: ruff resolves the policy "
            "per file, by walking up from the file to the repository-root "
            "ruff.toml, so the working directory of this process cannot change "
            "a verdict."
        ),
    )
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument(
        "--git-timeout",
        type=float,
        default=GIT_TIMEOUT_SECONDS,
        help=(f"Seconds each git invocation may run before it is killed and the gate fails (default: {GIT_TIMEOUT_SECONDS})."),
    )
    parser.add_argument(
        "--ruff-timeout",
        type=float,
        default=RUFF_TIMEOUT_SECONDS,
        help=(f"Seconds each ruff invocation may run before it is killed and the gate fails (default: {RUFF_TIMEOUT_SECONDS})."),
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
        raise GateError(f"command could not start: {_display_command(argv)}: {exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - pipes never closed
            pass
        raise CommandTimeout(f"command exceeded its {timeout:g}s budget and was killed: {_display_command(argv)}") from None
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


def _lintable(paths: Sequence[Path]) -> tuple[list[Path], list[tuple[str, str]]]:
    """Split changed files into (lintable, skipped-with-a-reason).

    The skipped list is never silent: every entry is a file ruff_scope has
    classified as not ours to lint, and the caller prints the reason.
    """
    lintable: list[Path] = []
    skipped: list[tuple[str, str]] = []
    for path in paths:
        relative = path.as_posix()
        reason = ruff_scope.excluded_reason(relative)
        if reason is None and not ruff_scope.is_lintable(relative):
            reason = "not first-party Python source (vendored, generated or tool state)"
        if reason is None:
            lintable.append(path)
        else:
            skipped.append((relative, reason))
    return lintable, skipped


def _authored_policy(repo_root: Path) -> dict[str, object]:
    """Read the repository-root policy, failing closed if it is not there."""
    policy_path = repo_root / ruff_scope.POLICY_CONFIG
    if not policy_path.is_file():
        raise GateError(
            f"the repository-wide ruff policy is missing: {policy_path} does not "
            "exist. Without it ruff falls back to its own defaults for every file "
            "outside backend/, which is the measurement hole this gate is "
            "supposed to be closed against. Add the policy; do not add "
            "--isolated."
        )
    try:
        return tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"{policy_path} is unreadable: {exc}") from exc


def _expected_policy(repo_root: Path) -> tuple[int, str, list[str]]:
    """The rule set the repository declares for itself, from its own config."""
    policy = _authored_policy(repo_root)
    line_length = policy.get("line-length")
    target_version = str(policy.get("target-version", ""))
    selected = list(policy.get("lint", {}).get("select", []))  # type: ignore[union-attr]
    if not isinstance(line_length, int) or not selected:
        raise GateError(f"{repo_root / ruff_scope.POLICY_CONFIG} must declare a numeric line-length and a non-empty lint.select; the gate refuses to verify a policy it cannot read.")
    return line_length, target_version, selected


def _version(value: str) -> tuple[int, ...]:
    """Normalise ruff's two spellings of a target version to one tuple.

    A configuration file writes ``target-version = "py312"`` while
    ``ruff check --show-settings`` reports ``3.12``, and the compact form packs
    the minor version into two digits, so ``py312`` is ``(3, 12)`` and not
    ``(312,)``.
    """
    text = value.strip().lower()
    if text.startswith("py"):
        text = text[2:]
    parts = text.split(".") if "." in text else [text[:1], text[1:3]]
    try:
        return tuple(int(part) for part in parts if part != "")
    except ValueError as exc:
        raise GateError(f"cannot read {value!r} as a Python version") from exc


def _verify_policy(
    repo_root: Path,
    representative: Path,
    *,
    ruff_cwd: Path,
    timeout: float,
) -> str:
    """Ask ruff which settings ``representative`` resolved, and enforce policy.

    Returns the settings path it resolved to.  Raises :class:`GateError` when
    the scope is not demonstrably on the repository policy, which is the
    failure mode that made 108 findings invisible: a file with no governing
    configuration, which ruff silently checks with its defaults.
    """
    line_length, target_version, selected = _expected_policy(repo_root)
    command = _ruff_command(ruff_cwd, "check", ["--show-settings", representative])
    try:
        result = run_command(command, cwd=ruff_cwd, timeout=timeout)
    except GateError as exc:
        raise GateError(f"could not verify the ruff policy: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"ruff --show-settings failed for {representative}: {detail}")
    text = result.stdout.decode("utf-8", errors="replace")
    match = SETTINGS_PATH_RE.search(text)
    if match is None:
        raise GateError(
            f"{representative} has no ruff configuration above it, so ruff is "
            "checking it with its built-in defaults instead of the "
            "repository policy. That is how findings outside backend/ went "
            f"unmeasured. Put the file under the {repo_root / ruff_scope.POLICY_CONFIG} "
            "policy, or classify it in scripts/ruff_scope.py with a reason."
        )
    settings_path = Path(match.group("path"))
    try:
        settings_path.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise GateError(f"{representative} resolves to {settings_path}, which is outside the repository: a config from outside the tree can change a verdict without being reviewable here.") from exc

    settings = {item.group("key"): item.group("value").strip() for item in SETTING_RE.finditer(text)}
    resolved_length = settings.get("linter.line_length", "")
    if resolved_length != str(line_length):
        raise GateError(f"{representative} resolves to line-length {resolved_length!r}, not the repository policy's {line_length}. A file must not be linted at a different line length from its neighbours.")
    resolved_target = settings.get("linter.unresolved_target_version", "")
    if target_version and resolved_target and _version(resolved_target) != _version(target_version):
        raise GateError(f"{representative} resolves to target-version {resolved_target!r}, not the repository policy's {target_version}.")
    for key in ("linter.exclude", "linter.per_file_ignores"):
        value = settings.get(key, "")
        if value not in ("[]", "{}"):
            raise GateError(
                f"{representative} resolves with {key} = {value}. This gate "
                "refuses to pass while the resolved policy hides findings "
                "behind a blanket exclude or a per-file pin: fix the code, or "
                "classify the scope in scripts/ruff_scope.py with a reason."
            )
    print(f"  policy for {representative.name}: {settings_path} (line-length {resolved_length}, target py{resolved_target}, select {'/'.join(selected)}, no exclude, no per-file-ignores)")
    return str(settings_path)


def _ruff_command(
    ruff_cwd: Path,
    subcommand: str,
    paths: Sequence[str | Path],
    *,
    isolated: bool = False,
) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        command = [uv, "run", "--no-sync", "ruff", subcommand]
    else:
        command = [sys.executable, "-m", "ruff", subcommand]
    if isolated:  # pragma: no cover - the blind spot this gate no longer has
        command.append("--isolated")
    command.extend(str(path) for path in paths)
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if bool(args.base_ref) != bool(args.head_ref):
        print("ERROR --base-ref and --head-ref must be provided together", file=sys.stderr)
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
        print("ERROR --git-timeout and --ruff-timeout must be positive", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    ruff_cwd = (args.backend_root or repo_root / "backend").resolve()
    try:
        changed = _changed_python_paths(
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

    paths, skipped = _lintable(changed)
    print(f"changed Python files: {len(changed)}")
    for path in changed:
        print(f"  {path.as_posix()}")
    for relative, reason in skipped:
        print(f"not linted: {relative}")
        print(f"  reason: {reason}")
    if not paths:
        print("incremental ruff gate: 0 (no lintable changed Python files)")
        return 0

    # Verify the policy once per configuration scope before trusting any
    # verdict, so a scope that silently fell back to ruff defaults cannot
    # report success.
    print(f"verifying the repository policy ({ruff_scope.POLICY_CONFIG}):")
    try:
        for representative in _scope_representatives([str((repo_root / path).resolve()) for path in paths]):
            _verify_policy(
                repo_root,
                Path(representative),
                ruff_cwd=ruff_cwd,
                timeout=args.ruff_timeout,
            )
    except GateError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    targets = [str((repo_root / path).resolve()) for path in paths]
    # Windows caps a whole command line at 32 767 characters, so a revision that
    # touches enough files must be handed to ruff in groups rather than as one
    # argv.  The union of the groups is exactly the changed lintable set.
    for subcommand, extra in (("check", []), ("format", ["--check"])):
        for index, chunk in enumerate(ruff_scope.chunk_paths(targets), start=1):
            command = _ruff_command(ruff_cwd, subcommand, [*extra, *chunk])
            print("running:", _display_command(command))
            try:
                result = run_command(command, cwd=ruff_cwd, timeout=args.ruff_timeout)
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
                print(
                    f"incremental ruff gate: FAILED (exit {result.returncode}, ruff {subcommand} {index}, repository policy)",
                    file=sys.stderr,
                )
                return result.returncode
    print("incremental ruff gate: 0")
    return 0


def _scope_representatives(paths: Sequence[str]) -> list[str]:
    """One file per configuration scope, shallowest first.

    Scopes are distinguished by which configuration ruff resolves for the file,
    and ruff walks up from the file's own directory, so two files in one
    directory always resolve identically while files in different directories
    may not.  Keying on the directory chain therefore cannot miss a scope; at
    worst it asks once more than strictly necessary.  Paths must be absolute:
    ruff is launched from ``ruff_cwd``, so a repository-relative path would be
    resolved against the wrong root.
    """
    representatives: list[str] = []
    seen_trees: list[tuple[str, ...]] = []
    for path in sorted(paths, key=lambda item: (len(Path(item).parts), item)):
        tree = Path(path).parts[:-1]
        if any(tree[: len(existing)] == existing for existing in seen_trees):
            continue
        seen_trees.append(tree)
        representatives.append(path)
    return representatives


if __name__ == "__main__":
    raise SystemExit(main())
