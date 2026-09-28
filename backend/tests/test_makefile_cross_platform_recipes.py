"""Backend `make` targets must actually run on Windows.

This Makefile sets `SHELL := cmd.exe` on Windows, so every recipe line is handed
to cmd.exe. Recipes were written with POSIX shell syntax, which cmd.exe cannot
parse:

* `PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest ...` - cmd.exe
  tries to execute a program literally named `PYTHONPATH=.` and fails with
  "'PYTHONPATH' is not recognized as an internal or external command". That broke
  `test`, `test-live`, `test-blocking-io`, `test-shard`, `gateway`, `dev` and
  `migrate-rev` - including the backend test command the developer guide tells a
  Windows user to run. It worked in CI only because CI is Linux.
* `if [ -z "$(MSG)" ]; then ... fi` in `migrate-rev` - a POSIX conditional, same
  problem.

`test-shard` had a second, independent failure that hit every platform: it hard-
errored when `.test_durations` was absent, and that baseline is not in the tree.
Since `.github/workflows/backend-unit-tests.yml` runs
`make test-shard SPLITS=4 GROUP=N`, all four shards died before collecting a
single test. A missing performance baseline must not decide whether tests run.

These tests run the real Makefile through `make -n` on this host, so they assert
the commands the user's shell would actually receive.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
MAKEFILE = BACKEND / "Makefile"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _dry_run(target: str) -> str:
    proc = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"make -n {target} failed: {proc.stderr}"
    return proc.stdout


# The targets that used to carry a POSIX env prefix.
ENV_PREFIX_TARGETS = ["test", "test-live", "test-blocking-io", "test-shard", "gateway", "dev", "migrate-rev"]


@pytest.mark.parametrize("target", ENV_PREFIX_TARGETS)
def test_no_target_emits_a_posix_env_prefix(target: str) -> None:
    """cmd.exe cannot run `VAR=value cmd`; on Windows each var needs `set`."""
    recipe = _dry_run(target)
    lines = [line.strip() for line in recipe.splitlines() if line.strip()]
    offenders = [line for line in lines if re.match(r"^(PYTHONPATH|ALPHA_HOME|ALPHA_RUN_LIVE_TESTS|ALPHA_[A-Z_]+)=", line)]
    assert not offenders, f"{target} emits a POSIX env prefix that cmd.exe cannot parse:\n" + "\n".join(offenders)


@pytest.mark.skipif(os.name != "nt", reason="the cmd.exe form is what breaks on Windows")
@pytest.mark.parametrize("target", ENV_PREFIX_TARGETS)
def test_on_windows_the_env_vars_use_the_cmd_syntax(target: str) -> None:
    recipe = _dry_run(target)
    assert 'set "PYTHONPATH=."' in recipe, f"{target} does not set PYTHONPATH the way cmd.exe needs:\n{recipe}"
    # And it must reach uv/pytest, not stop after the `set`s.
    assert "uv run" in recipe, recipe


@pytest.mark.parametrize("target", ENV_PREFIX_TARGETS)
def test_the_posix_branch_is_preserved(target: str) -> None:
    """The fix must not trade a broken Windows build for a broken Linux one.

    The POSIX side is what CI runs, so it is checked structurally: a
    `PYTHONPATH=.` prefix must still be reachable for the non-Windows branch.
    """
    source = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"^\s*RUN_ENV\s*=\s*PYTHONPATH=\.\s", source, re.MULTILINE), source
    assert re.search(r"^\s*RUN_ENV_LIVE\s*=\s*PYTHONPATH=\.", source, re.MULTILINE), source
    assert re.search(r"^\s*RUN_ENV_ALPHA_HOME\s*=\s*PYTHONPATH=\.", source, re.MULTILINE), source


def test_the_windows_branch_uses_the_quoted_set_form() -> None:
    source = MAKEFILE.read_text(encoding="utf-8")
    windows_branch = source[source.index("ifeq ($(OS),Windows_NT)") : source.index("\nendif", source.index("ifeq ($(OS),Windows_NT)"))]
    assert 'set "PYTHONPATH=."&&' in windows_branch
    # `set "VAR=value" &&` (space before &&) makes cmd.exe include the trailing
    # space in the value, which corrupts PYTHONPATH.
    assert re.search(r'set "[A-Z_]+=[^"]*"&&', windows_branch)
    assert not re.search(r'set "[A-Z_]+=[^"]*"\s+&&', windows_branch)


def test_test_shard_runs_even_without_the_duration_baseline() -> None:
    """The regression: all four CI shards died before collecting a test."""
    proc = subprocess.run(
        ["make", "-n", "--no-print-directory", "test-shard", "SPLITS=4", "GROUP=1"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    assert "is missing" in combined, combined
    # It must be a warning about balance, and the suite must still be invoked.
    assert "warning" in combined.lower(), combined
    assert "--splits 4" in proc.stdout and "--group 1" in proc.stdout, proc.stdout
    assert "pytest" in proc.stdout, proc.stdout
    # And it must not have bailed out with the old fatal message.
    assert "error: .test_durations is missing" not in combined


def test_a_missing_duration_baseline_does_not_request_duration_balancing() -> None:
    """Passing --splitting-algorithm least_duration with no baseline is a guess."""
    proc = subprocess.run(
        ["make", "-n", "--no-print-directory", "test-shard", "SPLITS=4", "GROUP=1"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    baseline = BACKEND / ".test_durations"
    if not baseline.exists():
        assert "least_duration" not in proc.stdout, proc.stdout
        assert "--durations-path" not in proc.stdout, proc.stdout


def test_migrate_rev_guard_is_not_a_posix_shell_conditional() -> None:
    """`if [ -z ... ]; then fi` is a syntax error in cmd.exe."""
    source = MAKEFILE.read_text(encoding="utf-8")
    migrate = source[source.index("migrate-rev:") :]
    migrate = migrate[: migrate.index("\n\n", 1)] if "\n\n" in migrate[1:] else migrate[:400]
    assert 'if [ -z "$(MSG)" ]' not in migrate
    # The usage line must still be reachable, and a missing MSG must fail.
    assert "migrate-rev MSG=" in migrate
    assert "@exit 1" in migrate
