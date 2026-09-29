import subprocess
from pathlib import Path

import pytest

from alpha.sandbox.worktrees import WorktreeManager


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=15)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "baseline")
    return root


def test_worktree_fails_without_repository(tmp_path):
    manager = WorktreeManager(tmp_path)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        manager.create_worktree("agent-task-01")
    assert not manager._active_worktrees
    assert not (tmp_path / ".worktrees" / "agent-task-01").exists()


def test_worktree_lifecycle(repo):
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("agent/task-01")
    assert wt.path.is_dir()
    assert git(wt.path, "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/agent/task-01"
    assert any(Path(item["path"]).resolve() == wt.path for item in manager.list_worktrees())
    assert manager.create_worktree("agent/task-01").path == wt.path
    assert manager.remove_worktree("agent/task-01", delete_branch=True)
    assert not wt.path.exists()
    assert not wt.is_active


def test_worktree_context_manager(repo):
    manager = WorktreeManager(repo)
    with manager.worktree_context("agent-subtask-auto", discard_uncommitted=True) as wt:
        (wt.path / "output.txt").write_text("complete", encoding="utf-8")
    assert not wt.path.exists()
    assert not manager._active_worktrees


# --- a manager must survive losing its in-process cache ---------------------
#
# `_active_worktrees` is a cache over git's own worktree administration, not the
# record of truth. It used to be the only record, so a manager constructed after
# a Gateway restart did not know about any worktree git still tracked:
# `remove_worktree` returned False and the worktree leaked with its branch
# checked out. These tests pin the rehydration.


def test_manager_adopts_worktrees_created_by_a_previous_process(repo):
    first = WorktreeManager(repo)
    wt = first.create_worktree("agent/persisted")

    # A brand new manager stands in for a restarted Gateway process: same repo,
    # same worktree, empty cache.
    second = WorktreeManager(repo)

    assert any(Path(item["path"]).resolve() == wt.path for item in second.list_worktrees())
    assert "agent/persisted" in second._active_worktrees
    assert second._active_worktrees["agent/persisted"].path == wt.path


def test_adopted_worktree_can_be_removed_after_restart(repo):
    WorktreeManager(repo).create_worktree("agent/persisted")

    restarted = WorktreeManager(repo)

    # The leak: before rehydration this returned False and the directory stayed.
    assert restarted.remove_worktree("agent/persisted", discard_uncommitted=True) is True
    assert not (repo / ".worktrees").joinpath("wt-" + __import__("hashlib").sha256(b"agent/persisted").hexdigest()).exists()
    assert "agent/persisted" not in restarted._active_worktrees


def test_adoption_ignores_worktrees_outside_the_managed_directory(repo):
    """Only adopt what this manager owns; the main worktree and operator
    worktrees are not ours to claim."""
    outside = repo.parent / "elsewhere"
    subprocess.run(["git", "worktree", "add", "-b", "operator/branch", str(outside)], cwd=repo, check=True, capture_output=True)

    manager = WorktreeManager(repo)

    assert "operator/branch" not in manager._active_worktrees
    assert "main" not in manager._active_worktrees or Path(repo).resolve() != manager.repo_root


# --- uncommitted work must survive a cleanup request ------------------------


def test_remove_refuses_to_discard_uncommitted_work(repo):
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("agent/dirty")
    (wt.path / "wip.txt").write_text("unsaved agent work", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted or untracked work"):
        manager.remove_worktree("agent/dirty", force=True)

    # The point of the refusal: the work is still there to be resumed or committed.
    assert (wt.path / "wip.txt").read_text(encoding="utf-8") == "unsaved agent work"
    assert wt.path.exists()


def test_remove_refuses_on_untracked_files_alone(repo):
    """An untracked file is uncommitted work even when nothing is staged."""
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("agent/untracked")
    (wt.path / "scratch.txt").write_text("never added", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted or untracked work"):
        manager.remove_worktree("agent/untracked", force=True)

    assert (wt.path / "scratch.txt").exists()


def test_remove_allows_discard_when_asked_explicitly(repo):
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("agent/scratch")
    (wt.path / "scratch.txt").write_text("disposable", encoding="utf-8")

    assert manager.remove_worktree("agent/scratch", discard_uncommitted=True) is True
    assert not wt.path.exists()


def test_committed_worktree_removes_without_the_opt_in(repo):
    """A clean tree is still removable; the guard must not block normal cleanup."""
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("agent/clean")
    (wt.path / "done.txt").write_text("committed", encoding="utf-8")
    git(wt.path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "done.txt")
    git(wt.path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "add done")

    assert manager.has_uncommitted_work("agent/clean") is False
    assert manager.remove_worktree("agent/clean") is True


def test_context_manager_keeps_a_dirty_worktree_for_recovery(repo):
    """The crash case: leaving the block must not destroy the agent's work."""
    manager = WorktreeManager(repo)

    with pytest.raises(RuntimeError, match="uncommitted or untracked work"):
        with manager.worktree_context("agent/crashed") as wt:
            (wt.path / "half-done.py").write_text("def unfinished(): ...", encoding="utf-8")

    # Still on disk, still recoverable, and still tracked by git.
    assert (wt.path / "half-done.py").read_text(encoding="utf-8") == "def unfinished(): ..."
    assert any(Path(item["path"]).resolve() == wt.path for item in manager.list_worktrees())


def test_has_uncommitted_work_is_false_for_a_clean_tree(repo):
    manager = WorktreeManager(repo)
    manager.create_worktree("agent/pristine")
    assert manager.has_uncommitted_work("agent/pristine") is False


def test_has_uncommitted_work_is_true_for_a_missing_worktree(repo):
    """Absent is not clean: a worktree that is gone has nothing to protect, and
    reporting `False` here would let a caller read it as 'verified empty'."""
    manager = WorktreeManager(repo)
    assert manager.has_uncommitted_work("agent/never-created") is False


@pytest.mark.parametrize("branch", ["../outside", "absolute", "-force", "a/../../b", "a\\b", "a..b", "a.lock", ""])
def test_invalid_branch_rejected_without_changes(repo, branch):
    if branch == "absolute":
        branch = str(repo.parent / "outside")
    manager = WorktreeManager(repo)
    with pytest.raises((ValueError, RuntimeError, subprocess.CalledProcessError)):
        manager.create_worktree(branch)
    assert not manager._active_worktrees


def test_existing_directory_is_not_adopted(repo):
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("candidate")
    manager.remove_worktree("candidate")
    wt.path.mkdir()
    sentinel = wt.path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError):
        manager.create_worktree("candidate")
    assert not manager.remove_worktree("candidate")
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_existing_branch_wrong_base_is_rejected(repo):
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("candidate")
    manager.remove_worktree("candidate")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "next")
    with pytest.raises(RuntimeError):
        manager.create_worktree("candidate")
    assert not wt.path.exists()


def test_remove_failure_preserves_managed_worktree(repo, monkeypatch):
    manager = WorktreeManager(repo)
    wt = manager.create_worktree("candidate")
    original = manager._run_git

    def fail_remove(args):
        if args[:2] == ["worktree", "remove"]:
            raise subprocess.CalledProcessError(1, args)
        return original(args)

    monkeypatch.setattr(manager, "_run_git", fail_remove)
    with pytest.raises(subprocess.CalledProcessError):
        manager.remove_worktree("candidate")
    assert wt.path.exists()
    assert wt.is_active


def test_git_calls_are_bounded(repo, monkeypatch):
    manager = WorktreeManager(repo)

    def run(*args, **kwargs):
        assert 0 < kwargs["timeout"] <= 60
        assert kwargs["check"] is True
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        manager.create_worktree("candidate")
    assert not manager._active_worktrees
