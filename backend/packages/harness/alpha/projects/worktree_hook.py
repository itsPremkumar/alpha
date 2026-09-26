"""Automatic Worktree Execution Lifecycle Hook (Cursor & OpenClaw style).

Enables coding agents to automatically execute tasks in isolated Git worktrees,
preventing parallel agents from clobbering each other's checkouts.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.projects.workspace import branch_name, lease_worktree, release_worktree
from alpha.sandbox.worktrees import WorktreeInstance

logger = logging.getLogger(__name__)

#: Wall-clock ceiling for the worktree diff. ``git diff`` is not a purely local
#: operation: an external diff/textconv driver is arbitrary configured code, and
#: a driver that hangs would pin the task that is releasing its worktree.
_DIFF_TIMEOUT_SECONDS = 60.0

#: Grace period for reaping the diff process once it has been killed. Bounds the
#: kill path so a survivor holding the pipes cannot turn a bounded diff into a
#: hang.
_DIFF_REAP_TIMEOUT_SECONDS = 5.0


class WorktreeDiffTimeout(TimeoutError):
    """A worktree diff exceeded its deadline; its git process was killed."""


def _kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate *process* and anything it started, best effort and bounded."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=_DIFF_REAP_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            # No ``taskkill`` (stripped image): fall back to the direct child.
            with contextlib.suppress(OSError):
                process.kill()
        return
    with contextlib.suppress(OSError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def _run_git_bounded(args: list[str], *, cwd: str, timeout: float, description: str) -> str:
    """Run a git command under a hard deadline and return its stdout.

    ``subprocess.run(timeout=...)`` kills only the direct child, and git itself
    runs hooks, external diff drivers and credential helpers: a survivor keeps
    the inherited pipes open, so on Windows the unbounded post-kill
    ``communicate()`` CPython performs would still block. Killing the whole group
    and reaping under its own bounded grace period makes the deadline actually
    terminate.
    """
    popen_kwargs: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(args, cwd=cwd, **popen_kwargs)  # noqa: S603 - fixed git argv, no shell
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            process.communicate(timeout=_DIFF_REAP_TIMEOUT_SECONDS)
        raise WorktreeDiffTimeout(f"{description} did not complete within {timeout:g}s and was killed") from None
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, args, output=stdout, stderr=stderr or "")
    return stdout or ""


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
    #: Set when patch generation itself failed, so "no patch" can be told apart
    #: from "no changes". The task outcome in ``error``/``success`` is unchanged.
    patch_error: str | None = None

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
            "patch_error": self.patch_error,
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
        cmd = ["git", "-C", str(self.instance.path), "diff", "HEAD"]
        return _run_git_bounded(
            cmd,
            cwd=str(self.instance.path),
            timeout=_DIFF_TIMEOUT_SECONDS,
            description="worktree diff",
        )

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool | None:
        patch_text = ""
        patch_gen = False
        patch_error: str | None = None
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
            except WorktreeDiffTimeout as e:
                # A killed diff is not the same as "no changes": record it so the
                # caller can tell an absent patch from a failed one.
                logger.warning("Diff generation timed out in worktree: %s", e)
                patch_error = str(e)
            except subprocess.CalledProcessError as e:
                logger.warning("Diff generation failed in worktree: %s", e)
                patch_error = f"worktree diff failed: {e}"
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
            patch_error=patch_error,
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
