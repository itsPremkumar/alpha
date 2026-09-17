"""Automatic Worktree Execution Lifecycle Hook (Cursor & OpenClaw style).

Enables coding agents to automatically execute tasks in isolated Git worktrees,
preventing parallel agents from clobbering each other's checkouts.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_workspace.projects.workspace import branch_name, lease_worktree, release_worktree
from agent_workspace.sandbox.worktrees import WorktreeInstance

logger = logging.getLogger(__name__)


@dataclass
class WorktreeTaskResult:
    task_id: str
    agent: str
    project_id: str
    worktree_path: Path
    branch_name: str
    patch_generated: bool = False
    patch_content: str = ""
    error: str | None = None
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "project_id": self.project_id,
            "worktree_path": str(self.worktree_path),
            "branch_name": self.branch_name,
            "patch_generated": self.patch_generated,
            "error": self.error,
            "success": self.success,
        }


class WorktreeTaskContext(AbstractContextManager):
    """Context manager leasing an isolated git worktree for a task and safely releasing it."""

    def __init__(
        self,
        repo_path: str | Path,
        agent: str,
        project_id: str,
        task_id: str,
        base_ref: str = "HEAD",
        cleanup_on_exit: bool = True,
        generate_patch_on_exit: bool = True,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.agent = agent
        self.project_id = project_id
        self.task_id = task_id
        self.base_ref = base_ref
        self.cleanup_on_exit = cleanup_on_exit
        self.generate_patch_on_exit = generate_patch_on_exit
        self.instance: WorktreeInstance | None = None
        self.branch: str = branch_name(agent, project_id, task_id)
        self.result: WorktreeTaskResult | None = None

    def __enter__(self) -> WorktreeInstance:
        self.instance = lease_worktree(
            self.repo_path,
            agent=self.agent,
            project_id=self.project_id,
            task_id=self.task_id,
            base_ref=self.base_ref,
        )
        logger.info(
            "Leased isolated worktree for %s on task %s: %s (branch: %s)",
            self.agent,
            self.task_id,
            self.instance.path,
            self.branch,
        )
        return self.instance

    def _generate_diff(self) -> str:
        if not self.instance or not self.instance.path.exists():
            return ""
        try:
            cmd = ["git", "-C", str(self.instance.path), "diff", "HEAD"]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            return proc.stdout if proc.returncode == 0 else ""
        except Exception:
            return ""

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        patch_text = ""
        patch_gen = False
        error_msg = str(exc_val) if exc_val else None
        success_val = exc_type is None

        if self.instance and self.generate_patch_on_exit:
            try:
                diff = self._generate_diff()
                if diff:
                    patch_text = diff
                    patch_gen = True
                    patch_file = self.instance.path / f"{self.task_id}.patch"
                    patch_file.write_text(diff, encoding="utf-8")
            except Exception as e:
                logger.debug("Diff generation failed in worktree: %s", e)

        self.result = WorktreeTaskResult(
            task_id=self.task_id,
            agent=self.agent,
            project_id=self.project_id,
            worktree_path=self.instance.path if self.instance else self.repo_path,
            branch_name=self.branch,
            patch_generated=patch_gen,
            patch_content=patch_text,
            error=error_msg,
            success=success_val,
        )

        if self.cleanup_on_exit and self.instance:
            try:
                release_worktree(self.repo_path, self.project_id, self.branch, force=True)
                logger.info("Released worktree branch %s", self.branch)
            except Exception as e:
                logger.warning("Failed to release worktree %s: %s", self.branch, e)

        return False  # Don't suppress exceptions


def run_in_worktree(
    repo_path: str | Path,
    agent: str,
    project_id: str,
    task_id: str,
    fn: Callable[[Path], Any],
    base_ref: str = "HEAD",
) -> tuple[Any, WorktreeTaskResult]:
    """Execute a callable inside an isolated worktree sandbox and return output + result."""
    with WorktreeTaskContext(repo_path, agent, project_id, task_id, base_ref=base_ref) as instance:
        out = fn(instance.path)
    # result is set on exit
    return out, WorktreeTaskResult(
        task_id=task_id,
        agent=agent,
        project_id=project_id,
        worktree_path=instance.path,
        branch_name=branch_name(agent, project_id, task_id),
        success=True,
    )
