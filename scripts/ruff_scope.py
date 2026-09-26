#!/usr/bin/env python3
"""The one definition of "the Python this repository lints".

WHY THIS MODULE EXISTS
----------------------
The incremental gate (``check_changed_python_lint.py``) and the Ruff debt
report (the ``ruff-debt-report`` job in ``.github/workflows/lint-check.yml``)
used to disagree about their file set, and the disagreement is invisible by
construction: a gate lints the files a revision touched, while a debt report
walks a directory.  When the two describe different directories, every finding
in the difference is counted nowhere.  That is exactly how 108 findings in
first-party non-backend code stayed invisible - the gate measured those files
under ruff's defaults and the debt report never walked them at all.

So the file set lives here, once, and both callers import it.  There is no
second copy of this list to drift.

WHAT IS IN SCOPE
----------------
Every ``.py`` file in the working tree that is not inside a vendored,
generated or tool-state directory.  That is the whole repository: ``backend/``,
``scripts/``, ``skills/``, ``tests/``, ``docker/``, ``examples/`` and anything
else added later.  All of it is first-party source that this project ships or
maintains, and all of it is now governed by one policy - the ``ruff.toml`` at
the repository root, which ``backend/ruff.toml`` extends.  A file is opted out
of linting only by appearing in :data:`EXCLUDED_FILES`, with a stated reason.

WHAT IS NOT IN SCOPE
--------------------
Directory names only.  Two lists, both explicit:

``VENDORED_DIRECTORIES``
    Ruff's own default file-resolver exclusions (``.venv``, ``node_modules``,
    ``dist``, ...) plus the tool-state and build-output directories this
    repository's ``.gitignore`` already keeps out of version control.  They
    are skipped by name because nothing in them is source, not because their
    findings are inconvenient.  A fresh CI checkout does not contain them, so
    the debt report measures the same file set there as it does locally.

    Nested virtual environments are additionally recognised by their
    ``pyvenv.cfg`` marker rather than by name, so an environment created
    anywhere in the tree (for example ``build/measure/prod-venv/``) is
    classified as third-party instead of being linted as if it were ours.  A
    bare ``ruff check .`` at the root will still show those files; this scope
    deliberately measures less than that, never more.

``EXCLUDED_FILES``
    Individual files, each with a written reason.  There is currently exactly
    one, and it is a template rather than a module: see that entry below.

NO BLANKET EXEMPTIONS
---------------------
There is no ``noqa``, no ``per-file-ignores`` and no blanket ``exclude`` for any
of the code covered here.  An unparseable file is reported as unparseable
rather than silenced, which is why a *root* ``ruff check .`` still shows the
``invalid-syntax`` on the template below even though the gate and the debt
report classify it as a template.  The classification is printed by both callers
so it cannot rot quietly.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

#: The repository-wide policy.  ``backend/ruff.toml`` extends it; ruff's
#: per-file settings discovery walks up to it, so every file this module
#: returns is linted with ``select = ["E", "F", "I", "UP"]`` at line-length 240.
POLICY_CONFIG = "ruff.toml"


@dataclass(frozen=True)
class ExcludedFile:
    """One file that is deliberately not linted, and why.

    ``path`` is repository-relative and POSIX-separated.  ``reason`` is printed
    by both the gate and the debt report: an exclusion nobody can see is a
    silent skip, and a silent skip is indistinguishable from a hidden finding.
    """

    path: str
    reason: str


EXCLUDED_FILES: tuple[ExcludedFile, ...] = (
    ExcludedFile(
        path=".agent/skills/blocking-io-guard/templates/anchor.template.py",
        reason=(
            "a copy-me template, not a module: its placeholder identifiers "
            "(def test_<entry_point>_offloads_blocking_io_on_<branch>) are not "
            "valid Python, so the file has no stable text for any rule to "
            "judge. It still reports as invalid-syntax under a bare "
            "'ruff check .', which is where a contributor sees it."
        ),
    ),
)

# Ruff's own default file-resolver exclusions, verbatim, so this module and
# ruff agree about what "not source" means.
_RUFF_DEFAULT_EXCLUDES = frozenset(
    {
        ".bzr",
        ".direnv",
        ".eggs",
        ".git",
        ".git-rewrite",
        ".hg",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".nox",
        ".pants.d",
        ".pyenv",
        ".pytest_cache",
        ".pytype",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        ".vscode",
        "__pypackages__",
        "_build",
        "buck-out",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)

# Directories this repository's .gitignore keeps out of version control.  They
# hold runtime state, caches, coverage output and machine-local agent state, so
# a fresh CI checkout has none of them and the debt report measures the same
# file set locally as it does in CI.
_REPO_STATE_DIRECTORIES = frozenset(
    {
        ".alpha",
        ".claude",
        ".agent-workspace",
        ".agent_workspace_projects",
        ".cache",
        ".claude",
        ".deerflow_projects",
        ".gstack",
        ".hypothesis",
        ".idea",
        ".log",
        ".logs",
        ".monocle",
        ".next",
        ".omc",
        ".playwright-mcp",
        ".pnpm-store",
        ".tmp",
        ".turbo",
        ".vscode-test",
        ".workbuddy-ai",
        ".worktrees",
        "__pycache__",
        "coverage",
        "htmlcov",
        "log",
        "logs",
        "temp",
        "tmp",
    }
)

#: Every directory name that is not source.  Nothing here is an exemption from
#: a rule: a directory in this set contains no first-party Python at all.
VENDORED_DIRECTORIES: frozenset[str] = _RUFF_DEFAULT_EXCLUDES | _REPO_STATE_DIRECTORIES

_EXCLUDED_PATHS: frozenset[str] = frozenset(item.path for item in EXCLUDED_FILES)


def excluded_reason(relative_path: str) -> str | None:
    """Return the stated reason ``relative_path`` is not linted, or ``None``."""
    for item in EXCLUDED_FILES:
        if item.path == relative_path:
            return item.reason
    return None


def is_lintable(relative_path: str) -> bool:
    """Is this repository-relative POSIX path a file the project lints?"""
    if not relative_path.endswith(".py"):
        return False
    if any(part in VENDORED_DIRECTORIES for part in relative_path.split("/")[:-1]):
        return False
    return relative_path not in _EXCLUDED_PATHS


def _is_vendored_environment(directory: Path) -> bool:
    """Is this directory an installed environment rather than project source?

    A directory carrying a ``pyvenv.cfg`` is a virtual environment, so
    everything under it is an installed third-party distribution.  Recognising
    the marker instead of the name catches environments created anywhere in the
    tree - ``build/measure/prod-venv/``, ``.tox/*/py/``, a colleague's
    ``scratch/.venv/`` - without a per-path exemption that would rot the moment
    somebody makes a new one.  The ``*.venv`` / ``*-venv`` name patterns are
    kept as a second signal for environments that were created without their
    marker, or whose marker was deleted.
    """
    if directory.name.endswith((".venv", "-venv")):
        return True
    return (directory / "pyvenv.cfg").is_file()


def _walk_python_files(repo_root: Path) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(repo_root):
        kept: list[str] = []
        for name in sorted(dirnames):
            if name in VENDORED_DIRECTORIES or name.endswith(".egg-info"):
                continue
            if _is_vendored_environment(Path(dirpath, name)):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            yield (Path(dirpath, name).relative_to(repo_root)).as_posix()


def lintable_python_files(repo_root: Path) -> list[str]:
    """Every lintable Python file in the tree, sorted, repository-relative.

    ``repo_root`` must already be resolved.  The result is the file set the
    debt report measures, and a superset of the file set the gate lints (the
    gate intersects it with the files a revision changed), so the two can never
    measure different rules or different policy.
    """
    return sorted(path for path in _walk_python_files(repo_root) if is_lintable(path))


def split_by_scope(repo_root: Path, relative_paths: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split repository-relative paths into (inside backend/, outside it)."""
    inside: list[str] = []
    outside: list[str] = []
    for path in relative_paths:
        (inside if path.startswith("backend/") else outside).append(path)
    return inside, outside


def report_exclusions(stream: TextIO | None = None) -> None:
    """Print the classification, so an exclusion is always visible."""
    for item in EXCLUDED_FILES:
        print(f"excluded from ruff scope: {item.path}", file=stream)
        print(f"  reason: {item.reason}", file=stream)


# Windows caps a whole command line at 32 767 characters, so the ~3 100 paths
# of the repository scope cannot be handed to ruff in one argv.  Both callers
# therefore split the scope with this, which keeps the command line well inside
# the platform limit instead of failing the measurement with WinError 206.
_PATH_BUDGET = 8_000 if sys.platform == "win32" else 100_000


def chunk_paths(paths: Sequence[str], *, budget: int = _PATH_BUDGET) -> list[list[str]]:
    """Split ``paths`` into groups whose joined length fits one command line.

    Order is preserved and no path is dropped or duplicated, so the union of
    the chunks is exactly the scope.  A single path longer than the budget is
    still emitted, alone, rather than being silently skipped.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0
    for path in paths:
        width = len(path) + 3  # two quotes and a separator
        if current and length + width > budget:
            chunks.append(current)
            current = []
            length = 0
        current.append(path)
        length += width
    if current:
        chunks.append(current)
    return chunks


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    files = lintable_python_files(root)
    backend, non_backend = split_by_scope(root, files)
    print(f"policy config: {POLICY_CONFIG} (project root: {root})")
    print(f"lintable python files: {len(files)} (backend={len(backend)} other={len(non_backend)})")
    report_exclusions()
    if "--list" in sys.argv[2:]:
        for path in files:
            print(path)
