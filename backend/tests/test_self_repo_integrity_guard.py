from pathlib import Path

from alpha.safety.self_repo_guard import SelfRepoGuard


def test_self_repo_guard_blocks_mutations(tmp_path: Path):
    harness_repo = tmp_path / "agent-workspace"
    harness_repo.mkdir()

    guard = SelfRepoGuard(running_repo_root=harness_repo)

    # Destructive mutations inside running repo root
    v1 = guard.evaluate_command("git checkout main", target_cwd=harness_repo)
    assert v1.is_violation is True
    assert "Self-repo mutation blocked" in v1.reason

    v2 = guard.evaluate_command("git reset --hard HEAD~1", target_cwd=harness_repo)
    assert v2.is_violation is True

    v3 = guard.evaluate_command("git clean -fd", target_cwd=harness_repo)
    assert v3.is_violation is True


def test_self_repo_guard_permits_safe_and_worktrees(tmp_path: Path):
    harness_repo = tmp_path / "agent-workspace"
    harness_repo.mkdir()
    worktree_dir = harness_repo / ".worktrees" / "subagent-1"
    worktree_dir.mkdir(parents=True)
    external_repo = tmp_path / "external-app"
    external_repo.mkdir()

    guard = SelfRepoGuard(running_repo_root=harness_repo)

    # 1. Read-only commands inside running repo are allowed
    assert guard.evaluate_command("git status", target_cwd=harness_repo).is_violation is False
    assert guard.evaluate_command("git log -n 5", target_cwd=harness_repo).is_violation is False
    assert guard.evaluate_command("git diff", target_cwd=harness_repo).is_violation is False

    # 2. Mutating git commands inside isolated .worktrees are allowed
    assert guard.evaluate_command("git checkout -b feature", target_cwd=worktree_dir).is_violation is False

    # 3. External repos are allowed
    assert guard.evaluate_command("git checkout main", target_cwd=external_repo).is_violation is False
    assert guard.evaluate_command(f"git -C {external_repo} reset --hard", target_cwd=harness_repo).is_violation is False


def test_self_repo_guard_override_mode(tmp_path: Path):
    harness_repo = tmp_path / "agent-workspace"
    harness_repo.mkdir()
    guard = SelfRepoGuard(running_repo_root=harness_repo, allow_self_mutation=True)

    assert guard.evaluate_command("git checkout main", target_cwd=harness_repo).is_violation is False
