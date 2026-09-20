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
    with manager.worktree_context("agent-subtask-auto") as wt:
        (wt.path / "output.txt").write_text("complete", encoding="utf-8")
    assert not wt.path.exists()
    assert not manager._active_worktrees


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
