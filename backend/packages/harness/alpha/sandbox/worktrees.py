from __future__ import annotations

import contextlib
import hashlib
import re
import subprocess
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.sandbox.env_policy import build_sandbox_env

_WORKTREE_LOCK = threading.RLock()


@dataclass
class WorktreeInstance:
    path: Path
    branch_name: str
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


class WorktreeManager:
    def __init__(self, repo_root: Path | str, base_worktree_dir: Path | str | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.worktrees_dir = Path(base_worktree_dir).resolve() if base_worktree_dir else self.repo_root / ".worktrees"
        self._active_worktrees: dict[str, WorktreeInstance] = {}

    def _target_path(self, branch_name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch_name) or ".." in branch_name:
            raise ValueError("Invalid worktree branch name")
        if any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in branch_name.split("/")):
            raise ValueError("Invalid worktree branch name")
        target = self.worktrees_dir / ("wt-" + hashlib.sha256(branch_name.encode()).hexdigest())
        if self.worktrees_dir.resolve() != self.worktrees_dir or target.resolve() != target or not target.resolve().is_relative_to(self.worktrees_dir):
            raise ValueError("Worktree path escapes managed directory")
        return target

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        env = {key: value for key, value in build_sandbox_env().items() if not key.upper().startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=60,
            env=env,
        )

    def _verify_worktree(self, target: Path, branch_name: str, base: str | None = None) -> dict[str, Any]:
        records = [item for item in self.list_worktrees() if Path(item["path"]).resolve() == target]
        if len(records) != 1 or records[0].get("branch") != f"refs/heads/{branch_name}":
            raise RuntimeError("Existing directory is not the requested repository worktree")
        if not target.is_dir() or target.resolve() != target or not (target / ".git").is_file():
            raise RuntimeError("Worktree directory identity is invalid")
        actual_root = self._run_git(["-C", str(target), "rev-parse", "--show-toplevel"]).stdout.strip()
        actual_branch = self._run_git(["-C", str(target), "symbolic-ref", "HEAD"]).stdout.strip()
        common = self._run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip()
        actual_common = self._run_git(["-C", str(target), "rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip()
        if Path(actual_root).resolve() != target or actual_branch != f"refs/heads/{branch_name}" or Path(common).resolve() != Path(actual_common).resolve():
            raise RuntimeError("Worktree repository identity mismatch")
        if base is not None and records[0].get("head") != base:
            raise RuntimeError("Existing worktree does not match requested base")
        return records[0]

    def create_worktree(self, branch_name: str, base_ref: str = "HEAD") -> WorktreeInstance:
        target = self._target_path(branch_name)
        if not base_ref or base_ref.startswith("-"):
            raise ValueError("Invalid worktree base reference")
        with _WORKTREE_LOCK:
            self._run_git(["check-ref-format", f"refs/heads/{branch_name}"])
            base = self._run_git(["rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}"]).stdout.strip()
            if target.exists():
                self._verify_worktree(target, branch_name, base)
            else:
                refs = self._run_git(["for-each-ref", "--format=%(refname) %(objectname)", f"refs/heads/{branch_name}"]).stdout.splitlines()
                existing = [line.split(" ", 1)[1] for line in refs if line.startswith(f"refs/heads/{branch_name} ")]
                if existing and existing != [base]:
                    raise RuntimeError("Existing branch does not match requested base")
                self.worktrees_dir.mkdir(parents=True, exist_ok=True)
                self._target_path(branch_name)
                if existing:
                    self._run_git(["worktree", "add", "--", str(target), branch_name])
                else:
                    self._run_git(["worktree", "add", "-b", branch_name, "--", str(target), base])
                self._verify_worktree(target, branch_name, base)
            wt = self._active_worktrees.get(branch_name) or WorktreeInstance(target, branch_name)
            self._active_worktrees[branch_name] = wt
            return wt

    def remove_worktree(self, branch_name: str, force: bool = True, delete_branch: bool = False) -> bool:
        target = self._target_path(branch_name)
        with _WORKTREE_LOCK:
            if not target.exists():
                return False
            try:
                self._verify_worktree(target, branch_name)
            except RuntimeError:
                return False
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            self._run_git([*args, "--", str(target)])
            if target.exists() or any(Path(item["path"]).resolve() == target for item in self.list_worktrees()):
                raise RuntimeError("Git did not remove the managed worktree")
            if delete_branch:
                self._run_git(["branch", "-D", "--", branch_name])
            wt = self._active_worktrees.pop(branch_name, None)
            if wt:
                wt.is_active = False
            return True

    def list_worktrees(self) -> list[dict[str, Any]]:
        proc = self._run_git(["worktree", "list", "--porcelain", "-z"])
        results: list[dict[str, Any]] = []
        current: dict[str, Any] = {}
        for line in proc.stdout.split("\0"):
            if line.startswith("worktree "):
                if current:
                    results.append(current)
                current = {"path": line[9:]}
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
        if current:
            results.append(current)
        return results

    @contextlib.contextmanager
    def worktree_context(self, branch_name: str, base_ref: str = "HEAD", delete_on_exit: bool = True) -> Generator[WorktreeInstance, None, None]:
        wt = self.create_worktree(branch_name, base_ref=base_ref)
        try:
            yield wt
        finally:
            if delete_on_exit and not self.remove_worktree(branch_name, force=True):
                raise RuntimeError("Managed worktree cleanup could not be verified")
