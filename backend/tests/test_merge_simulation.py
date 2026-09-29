"""Tests for merge simulation against a real repository.

These build actual branches and actual conflicts rather than stubbing git,
because the distinctions under test — clean vs conflicted vs failed, and
"never report a conflict that git did not report" — are distinctions git
makes, and a mock would only assert that the code agrees with itself.
"""

from __future__ import annotations

import subprocess

import pytest

from alpha.sandbox.merge_simulation import MergeOutcome, simulate_merge
from alpha.sandbox.worktrees import WorktreeManager


def git(root, *args, check: bool = True):
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True, timeout=30)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "file.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "base")
    return root


def make_branch(repo, name, content, message="change"):
    git(repo, "checkout", "-b", name)
    (repo / "file.txt").write_text(content, encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", message)
    git(repo, "checkout", "main")
    return name


# --- the three outcomes are genuinely different states ----------------------


def test_clean_merge_is_reported_clean(repo):
    branch = make_branch(repo, "feature/add-line", "base\nadded\n")

    sim = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref=branch, name="sim-clean")

    assert sim.outcome is MergeOutcome.CLEAN
    assert sim.is_mergeable is True
    assert sim.conflicted_files == ()


def test_conflicting_merge_names_the_files(repo):
    make_branch(repo, "feature/other", "theirs\n")
    (repo / "file.txt").write_text("ours\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "main moves the same line")

    sim = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="feature/other", name="sim-conflict")

    assert sim.outcome is MergeOutcome.CONFLICTED
    assert sim.is_mergeable is False
    assert "file.txt" in sim.conflicted_files


def test_a_failure_is_not_reported_as_a_conflict(repo):
    """A missing candidate ref has no resolution to apply. Calling it a
    conflict sends an operator hunting for merge markers that do not exist."""
    sim = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="feature/does-not-exist", name="sim-missing")

    assert sim.outcome is MergeOutcome.FAILED
    assert sim.conflicted_files == ()
    assert "does-not-exist" in sim.reason


def test_unresolvable_base_is_a_failure_not_a_conflict(repo):
    sim = simulate_merge(WorktreeManager(repo), base_ref="no-such-base", head_ref="main", name="sim-nobase")

    assert sim.outcome is MergeOutcome.FAILED
    assert sim.base_sha is None


def test_unsafe_candidate_ref_is_refused_without_touching_git(repo):
    """A ref that could be read as an option must never reach git."""
    sim = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="--upload-pack=evil", name="sim-evil")

    assert sim.outcome is MergeOutcome.FAILED
    assert "unsafe" in sim.reason


# --- main is never touched ---------------------------------------------------


def test_simulation_does_not_move_main(repo):
    before = git(repo, "rev-parse", "main").stdout.strip()
    make_branch(repo, "feature/x", "base\nx\n")

    simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="feature/x", name="sim-nomove")

    assert git(repo, "rev-parse", "main").stdout.strip() == before


def test_simulation_does_not_leave_the_main_worktree_dirty(repo):
    make_branch(repo, "feature/conflict", "theirs\n")
    (repo / "file.txt").write_text("ours\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "conflicting main change")

    simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="feature/conflict", name="sim-cleanup")

    assert git(repo, "status", "--porcelain").stdout.strip() == ""
    assert not (repo / "file.txt").read_text(encoding="utf-8").startswith("<<<<<<<")


def test_simulation_removes_its_worktree(repo):
    manager = WorktreeManager(repo)
    make_branch(repo, "feature/y", "base\ny\n")

    simulate_merge(manager, base_ref="main", head_ref="feature/y", name="sim-removed")

    assert not any("sim-removed" in item["path"] for item in manager.list_worktrees())


def test_simulation_worktree_is_removed_even_when_the_merge_fails(repo):
    manager = WorktreeManager(repo)
    make_branch(repo, "feature/z", "theirs\n")
    (repo / "file.txt").write_text("ours\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "conflict on main")

    simulate_merge(manager, base_ref="main", head_ref="feature/z", name="sim-conflict-cleanup")

    assert not any("sim-conflict-cleanup" in item["path"] for item in manager.list_worktrees())


# --- honesty of the claim ---------------------------------------------------


def test_clean_merge_does_not_claim_to_be_verified(repo):
    """The single most important property here: git found no textual conflict.
    That is not a correctness or safety verdict, and the text must not imply one."""
    branch = make_branch(repo, "feature/safe-looking", "base\nfine\n")

    text = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref=branch, name="sim-honest").disclosure()

    assert "NOT that the result is correct" in text
    assert "no textual conflict" in text


def test_conflict_disclosure_names_the_files_and_warns_about_correctness(repo):
    make_branch(repo, "feature/c", "theirs\n")
    (repo / "file.txt").write_text("ours\n", encoding="utf-8")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "conflicting change")

    text = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="feature/c", name="sim-d").disclosure()

    assert "CONFLICTS" in text
    assert "file.txt" in text
    assert "not necessarily a correct one" in text


def test_failure_disclosure_states_the_reason(repo):
    sim = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref="nope", name="sim-f")
    assert "could not be completed" in sim.disclosure()


def test_base_movement_is_reported_rather_than_hidden(repo):
    """A merge judged against a base that has since moved describes a base
    that no longer exists. That staleness is a result, not a detail."""
    manager = WorktreeManager(repo)
    branch = make_branch(repo, "feature/moving", "base\nm\n")
    sim = simulate_merge(manager, base_ref="main", head_ref=branch, name="sim-stable")

    assert sim.base_unchanged is True
    assert sim.base_sha == git(repo, "rev-parse", "main").stdout.strip()


def test_result_round_trips_to_dict(repo):
    branch = make_branch(repo, "feature/dict", "base\nd\n")
    data = simulate_merge(WorktreeManager(repo), base_ref="main", head_ref=branch, name="sim-dict").to_dict()

    assert data["outcome"] == "clean"
    assert data["is_mergeable"] is True
    assert data["head_sha"]


# --- detached worktree plumbing ---------------------------------------------


def test_detached_worktree_has_no_branch(repo):
    manager = WorktreeManager(repo)
    path = manager.create_detached_worktree("sim-nobranch", "main")

    assert git(path, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0
    assert manager.remove_detached_worktree("sim-nobranch") is True


def test_detached_worktree_rejects_a_moving_base(repo):
    manager = WorktreeManager(repo)
    manager.create_detached_worktree("sim-pinned", "main")
    try:
        (repo / "new.txt").write_text("moved\n", encoding="utf-8")
        git(repo, "add", "new.txt")
        git(repo, "commit", "-m", "main advances")

        with pytest.raises(RuntimeError, match="does not match the requested base"):
            manager.create_detached_worktree("sim-pinned", "main")
    finally:
        manager.remove_detached_worktree("sim-pinned")


def test_detached_worktree_name_is_validated(repo):
    manager = WorktreeManager(repo)
    with pytest.raises(ValueError, match="Invalid simulation worktree name"):
        manager.create_detached_worktree("../escape", "main")


def test_removing_an_absent_simulation_worktree_reports_false(repo):
    assert WorktreeManager(repo).remove_detached_worktree("sim-never-existed") is False


def test_manager_exposes_the_simulation_as_a_method(repo):
    """A caller holding only a manager should not need to know which sibling
    module owns the capability."""
    branch = make_branch(repo, "feature/via-manager", "base\nvia\n")

    sim = WorktreeManager(repo).simulate_merge(base_ref="main", head_ref=branch, name="sim-via-manager")

    assert sim.outcome is MergeOutcome.CLEAN
    assert sim.is_mergeable is True
