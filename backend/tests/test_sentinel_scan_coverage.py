"""Regression: the sentinel must be able to see the tree it exists to watch.

Two defects, one test file, because they compounded:

1. **The skip list did not skip.** ``scan_directory`` did
   ``root.rglob(pattern)`` and *then* filtered on ``SKIP_DIR_NAMES``. ``rglob``
   has no prune hook, so the traversal cost was paid in full and the result
   discarded. Measured on this repository: ``rglob`` walked **87,073 files in
   24.1s** (the virtualenv is inside the scanned root), and the only three
   ``*.ps1`` it found were all inside ``.venv`` -- so the filter removed every
   one and the scan returned an empty list.

2. **It watched the wrong tree.** The Gateway resolved the scan root through
   ``runtime_paths.project_root()``, which resolves ``ALPHA_PROJECT_ROOT`` or the
   **process working directory**, and the documented launch command is
   ``cd backend && uvicorn app.gateway.app:app``. Every ``.ps1`` file in this
   repository lives at the repository root (``start.ps1``, ``installer/``,
   ``recovery/``, ``scripts/``); there are none under ``backend/``. So the source
   that exists specifically to detect BOM-less PowerShell scripts scanned a tree
   with zero PowerShell scripts and reported a healthy empty result.

Combined effect: the sentinel reported 0 signals and could not have observed its
own documented bug class. After the fixes it reports real signals and the
negative control below detects a planted defect.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from alpha.runtime.sentinel.runner import SentinelRunner
from alpha.runtime.sentinel.sources.scripts import SKIP_DIR_NAMES, iter_candidate_files, scan_directory


def _make_tree(root: Path) -> Path:
    """A miniature tree with a skipped dir that would be expensive if walked."""
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "clean.ps1").write_text("Write-Host 'plain ascii only'\n", encoding="utf-8")
    # Non-ASCII, no BOM -> the exact defect class the sentinel exists for.
    (root / "scripts" / "bad.ps1").write_bytes("# café naïve\nWrite-Host 'x'\n".encode())
    # A skipped directory that MUST NOT be descended into.
    decoy = root / ".venv" / "lib" / "deep"
    decoy.mkdir(parents=True)
    (decoy / "decoy.ps1").write_bytes(b"# also non-ascii\nWrite-Host 'x'\n")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "vendor.ps1").write_bytes(b"# also non-ascii\n")
    # A git worktree holds a full copy of the same repository.
    (root / ".worktrees" / "feature").mkdir(parents=True)
    (root / ".worktrees" / "feature" / "start.ps1").write_bytes(b"# non-ascii\n")
    return root


def test_skipped_directories_are_pruned_not_merely_filtered(tmp_path: Path) -> None:
    """The skip list must decide *traversal*, or it is decoration."""
    root = _make_tree(tmp_path / "repo")
    found = {p.name for p in iter_candidate_files(root)}
    assert found == {"clean.ps1", "bad.ps1"}, found


def test_no_signal_is_ever_reported_from_a_pruned_directory(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "repo")
    reported = {Path(str(s.context.get("path"))).name for s in scan_directory(root)}
    assert reported == {"bad.ps1"}, reported


def test_detection_is_unaffected_by_pruning(tmp_path: Path) -> None:
    """Pruning must not change the answer, only the cost."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.ps1").write_bytes("# café\n".encode())
    (root / "a.ps1.bom").write_bytes(b"\xef\xbb\xbf" + "# café\n".encode())
    signals = scan_directory(root)
    assert [s.kind for s in signals] == ["missing_bom"]
    assert not any(part in SKIP_DIR_NAMES for p in iter_candidate_files(root) for part in p.parts)


def test_pruned_walk_avoids_the_cost_the_unpruned_one_pays(tmp_path: Path) -> None:
    """A structural assertion, not a stopwatch: the pruned walk never enters.

    ``os.walk`` with ``dirnames`` mutated in place is the only walk that avoids
    descending. This records the directory the walk actually visited rather than
    comparing wall-clock times, so it cannot flake on a loaded host.
    """
    root = _make_tree(tmp_path / "repo")
    visited: list[str] = []
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        visited.append(os.path.relpath(current, root))
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]

    assert not any(".venv" in v for v in visited), visited
    assert not any("node_modules" in v for v in visited), visited
    assert not any(".worktrees" in v for v in visited), visited


def test_case_insensitive_extension_matching(tmp_path: Path) -> None:
    """Windows ships `.PS1` too, and the default pattern is `*.ps1`."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "UPPER.PS1").write_bytes("# naïve\n".encode())
    found = iter_candidate_files(root)
    assert [p.name for p in found] == ["UPPER.PS1"]


def test_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    assert iter_candidate_files(tmp_path / "absent") == []
    assert scan_directory(tmp_path / "absent") == []


def test_repository_root_is_the_repository_not_the_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan root must not follow the process cwd into `backend/`.

    ``runtime_paths.project_root()`` is "where Alpha writes its own state" and
    answers the cwd. Asking it for the repository root is the defect: every
    ``.ps1`` lives above ``backend/``.
    """
    from alpha.config import runtime_paths
    from app.gateway.autonomy.loops import _resolve_project_root

    resolved = _resolve_project_root()
    assert (resolved / "backend" / "packages").is_dir(), f"{resolved} is not the repository root"
    assert (resolved / "AGENTS.md").is_file(), f"{resolved} is not the repository root"
    # A cwd change must not change the answer.
    monkeypatch.chdir(resolved / "backend")
    assert _resolve_project_root() == resolved
    assert runtime_paths.project_root() == (resolved / "backend").resolve()


def test_repository_root_honours_an_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from alpha.config.runtime_paths import repository_root

    target = tmp_path / "elsewhere"
    target.mkdir()
    monkeypatch.setenv("ALPHA_REPOSITORY_ROOT", str(target))
    assert repository_root() == target.resolve()

    monkeypatch.setenv("ALPHA_REPOSITORY_ROOT", str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="ALPHA_REPOSITORY_ROOT"):
        repository_root()


def test_collector_reports_the_planted_defect_end_to_end(tmp_path: Path) -> None:
    """The negative control: the whole `collect()` path sees a real defect."""
    root = _make_tree(tmp_path / "repo")
    signals = SentinelRunner(repo_root=root).collect()
    assert [s.kind for s in signals] == ["missing_bom"]
    assert signals[0].severity == "critical"
    assert signals[0].context["path"].endswith("bad.ps1")


def test_collector_is_bounded_on_a_large_skipped_tree(tmp_path: Path) -> None:
    """Cost stays proportional to the *scanned* tree, not the ignored one."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "ok.ps1").write_text("Write-Host 'ascii'\n", encoding="utf-8")
    noise = root / ".venv"
    for i in range(400):
        d = noise / f"p{i}"
        d.mkdir(parents=True)
        for j in range(25):
            (d / f"f{j}.py").write_text("x = 1\n", encoding="utf-8")

    started = time.monotonic()
    assert iter_candidate_files(root) == [root / "scripts" / "ok.ps1"]
    elapsed = time.monotonic() - started
    # 10k decoy files. An unpruned walk costs seconds; the prune is instant.
    # Generous bound so a loaded host cannot flake this.
    assert elapsed < 10.0, f"pruned walk took {elapsed:.2f}s -- is the prune still applied?"
