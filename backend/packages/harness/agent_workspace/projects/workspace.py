"""Standard project workspace layout plus git worktree leases for parallel agents.

Every project gets the same isolated tree. Coding agents work in leased git
worktrees (via the existing WorktreeManager) — never in the same checkout.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

WORKSPACE_DIRS = (
    "repository",
    "documents",
    "specifications",
    "architecture",
    "research",
    "decisions",
    "tasks",
    "kanban",
    "communication",
    "artifacts",
    "tests",
    "reports",
    "memory",
    "project-config",
    "worktrees",
)

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _projects_root() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "projects"


def project_root(project_id: str) -> Path:
    return _projects_root() / project_id


def ensure_workspace(project_id: str) -> Path:
    root = project_root(project_id)
    for sub in WORKSPACE_DIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def branch_name(agent: str, project_id: str, task_id: str) -> str:
    def safe(s: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-").strip(".")[:40]
        if cleaned in ("", ".", ".."):
            return "x"
        return cleaned

    name = f"agent/{safe(agent)}/{safe(project_id)}/{safe(task_id)}"
    if not _BRANCH_RE.match(name) or ".." in name.split("/"):
        raise ValueError(f"Cannot build a safe branch name from {agent}/{project_id}/{task_id}")
    return name


def lease_worktree(repo_path: str | Path, *, agent: str, project_id: str, task_id: str, base_ref: str = "HEAD"):
    """Create an isolated worktree for one agent task. Caller must release it."""
    from agent_workspace.sandbox.worktrees import WorktreeManager

    root = ensure_workspace(project_id)
    manager = WorktreeManager(repo_root=repo_path, base_worktree_dir=root / "worktrees")
    return manager.create_worktree(branch_name(agent, project_id, task_id), base_ref)


def release_worktree(repo_path: str | Path, project_id: str, branch: str, *, force: bool = True) -> bool:
    from agent_workspace.sandbox.worktrees import WorktreeManager

    root = project_root(project_id)
    manager = WorktreeManager(repo_root=repo_path, base_worktree_dir=root / "worktrees")
    try:
        manager.remove_worktree(branch, force=force)
        return True
    except Exception:
        logger.warning("Worktree release failed for %s", branch, exc_info=True)
        return False


def with_task_worktree(
    repo_path: str | Path,
    agent: str,
    project_id: str,
    task_id: str,
    base_ref: str = "HEAD",
):
    """Context manager for automatic worktree task execution."""
    from agent_workspace.projects.worktree_hook import WorktreeTaskContext

    return WorktreeTaskContext(
        repo_path=repo_path,
        agent=agent,
        project_id=project_id,
        task_id=task_id,
        base_ref=base_ref,
    )

