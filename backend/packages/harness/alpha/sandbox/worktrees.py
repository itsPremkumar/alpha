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
from typing import TYPE_CHECKING, Any

from alpha.sandbox.env_policy import build_sandbox_env

if TYPE_CHECKING:
    # Type-only: `merge_simulation` is built on this class, so a runtime import
    # here would be circular. The delegation below imports it lazily.
    from alpha.sandbox.merge_simulation import MergeSimulation

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
        # `_active_worktrees` is a *cache*, not the record of truth. Git's own
        # administrative files under .git/worktrees are what actually own a
        # worktree, and they outlive this process. A manager built after a
        # Gateway restart must adopt the worktrees git still tracks, or
        # `remove_worktree` cannot find them, reports `False`, and the worktree
        # leaks with its branch still checked out.
        self._adopt_existing_worktrees()

    def _adopt_existing_worktrees(self) -> None:
        """Rehydrate the cache from git for worktrees inside our managed directory."""
        try:
            records = self.list_worktrees()
        except (subprocess.CalledProcessError, OSError, FileNotFoundError):
            # A repository that cannot be interrogated yet (not initialised, git
            # absent) simply has nothing to adopt. This is an absence, not a
            # failure, and must not be reported as a populated inventory.
            return
        for record in records:
            path = record.get("path")
            branch_ref = record.get("branch")
            if not path or not branch_ref:
                # A worktree with no branch is a detached or locked-out record;
                # it is not a managed, branch-owned worktree we may adopt.
                continue
            branch_name = branch_ref.removeprefix("refs/heads/")
            try:
                target = Path(path).resolve()
            except OSError:
                continue
            if target == self.repo_root or not target.is_relative_to(self.worktrees_dir):
                # Only adopt what lives under the directory this manager owns.
                # The main worktree and any operator-created worktree are not ours.
                continue
            self._active_worktrees.setdefault(branch_name, WorktreeInstance(target, branch_name))

    def _target_path(self, branch_name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch_name) or ".." in branch_name:
            raise ValueError("Invalid worktree branch name")
        if any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in branch_name.split("/")):
            raise ValueError("Invalid worktree branch name")
        target = self.worktrees_dir / ("wt-" + hashlib.sha256(branch_name.encode()).hexdigest())
        if self.worktrees_dir.resolve() != self.worktrees_dir or target.resolve() != target or not target.resolve().is_relative_to(self.worktrees_dir):
            raise ValueError("Worktree path escapes managed directory")
        return target

    def _simulation_path(self, name: str) -> Path:
        """Path for a detached simulation worktree, validated like any other.

        Kept in the same managed directory as branch worktrees so the escape
        check is identical; only the on-disk name differs, so a simulation
        tree is never mistaken for an implementation workspace when the
        directory is listed.
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) or ".." in name:
            raise ValueError("Invalid simulation worktree name")
        target = self.worktrees_dir / "sim" / ("sim-" + hashlib.sha256(name.encode()).hexdigest()[:16])
        if not target.resolve().is_relative_to(self.worktrees_dir):
            raise ValueError("Simulation worktree path escapes managed directory")
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

    def has_uncommitted_work(self, branch_name: str) -> bool:
        """True when the managed worktree holds work git does not already track.

        Untracked files count. A crash mid-task leaves exactly this state, and
        it is the only thing `worktree remove --force` will destroy silently.
        """
        target = self._target_path(branch_name)
        if not target.is_dir():
            return False
        try:
            status = self._run_git(["-C", str(target), "status", "--porcelain"]).stdout
        except (subprocess.CalledProcessError, OSError, FileNotFoundError):
            # An unreadable status is not evidence of a clean tree.
            return True
        return bool(status.strip())

    # ── detached simulation worktrees ────────────────────────────────────────
    #
    # A merge simulation needs a tree checked out at a commit with no branch
    # behind it, so that "merge candidate into base" can be attempted without
    # either ref moving. `create_worktree` cannot express that — it always
    # creates or reuses a branch — so these two methods exist separately rather
    # than overloading it with a flag that would make the branch cases harder
    # to read.

    def create_detached_worktree(self, name: str, base_ref: str) -> Path:
        """Create (or reuse) a detached worktree at ``base_ref``.

        Detached is the point: the tree has no branch, so nothing in the
        simulation can be pushed, and a stray commit made inside it belongs to
        no ref. ``base_ref`` is resolved to a commit first so a moving branch
        cannot change the base halfway through a simulation.
        """
        target = self._simulation_path(name)
        with _WORKTREE_LOCK:
            if not base_ref or base_ref.startswith("-"):
                raise ValueError("Invalid simulation base reference")
            # `--end-of-options` and the `^{commit}` peel keep a ref name from
            # being read as an option or accepted when it is not a commit.
            base = self._run_git(["rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}"]).stdout.strip()
            if target.exists():
                recorded = [item for item in self.list_worktrees() if Path(item["path"]).resolve() == target]
                if len(recorded) != 1:
                    raise RuntimeError("Existing simulation path is not a registered worktree")
                if recorded[0].get("head") != base:
                    raise RuntimeError("Existing simulation worktree does not match the requested base")
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(["worktree", "add", "--detach", "--", str(target), base])
            if not target.is_dir():
                raise RuntimeError("Git did not create the simulation worktree")
            return target

    def remove_detached_worktree(self, name: str) -> bool:
        """Remove a simulation worktree. It holds no agent work by contract."""
        target = self._simulation_path(name)
        with _WORKTREE_LOCK:
            if not target.exists():
                return False
            self._run_git(["worktree", "remove", "--force", "--", str(target)])
            if target.exists():
                raise RuntimeError("Git did not remove the simulation worktree")
            return True

    def simulate_merge(self, *, base_ref: str, head_ref: str, name: str) -> MergeSimulation:
        """Judge whether ``head_ref`` can be joined into ``base_ref``.

        A thin convenience so a caller holding a manager does not also have to
        know which sibling module owns the simulation. The dependency is
        deliberately local and one-way: ``merge_simulation`` is built on this
        class, so importing it at module scope here would be circular, and
        keeping the edge `worktrees -> merge_simulation` (rather than the
        reverse) is what stops this low-level git primitive from growing a
        dependency on the policy layer that sits above it.
        """
        from alpha.sandbox.merge_simulation import simulate_merge as _simulate

        return _simulate(self, base_ref=base_ref, head_ref=head_ref, name=name)

    def run_in_worktree(self, path: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a git command inside a specific worktree.

        Exists because ``_run_git`` is rooted at ``repo_root``; a simulation has
        to run its merge *inside the simulation tree* or it would merge into
        the main worktree, which is the one thing this whole path exists to
        avoid. ``check=False`` is required for merge simulation, whose conflicts
        are a normal outcome that must be inspected rather than raised.
        """
        env = {key: value for key, value in build_sandbox_env().items() if not key.upper().startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=check,
            timeout=120,
            env=env,
        )

    def remove_worktree(
        self,
        branch_name: str,
        force: bool = True,
        delete_branch: bool = False,
        *,
        discard_uncommitted: bool = False,
    ) -> bool:
        target = self._target_path(branch_name)
        with _WORKTREE_LOCK:
            if not target.exists():
                return False
            try:
                self._verify_worktree(target, branch_name)
            except RuntimeError:
                return False
            if not discard_uncommitted and self.has_uncommitted_work(branch_name):
                # `force` means "override git's refusal because the worktree is
                # busy or dirty". It is a convenience for a worktree this
                # manager owns, not permission to delete an agent's unsaved
                # work. Refusing here is what makes a crash resumable; a caller
                # that genuinely means to discard must say so.
                raise RuntimeError(
                    f"Refusing to remove worktree {branch_name!r}: it holds uncommitted or untracked work. "
                    "Commit or stash it first, or pass discard_uncommitted=True to destroy it deliberately."
                )
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
    def worktree_context(
        self,
        branch_name: str,
        base_ref: str = "HEAD",
        delete_on_exit: bool = True,
        *,
        discard_uncommitted: bool = False,
    ) -> Generator[WorktreeInstance, None, None]:
        """Create a worktree for the duration of the block, then clean it up.

        ``delete_on_exit`` is a *request* to remove the worktree, not a licence
        to destroy work in it. A block that ends with uncommitted or untracked
        changes leaves the worktree in place and says so, so a crashed or
        interrupted task stays recoverable instead of being silently deleted on
        the way out. Pass ``discard_uncommitted=True`` only for a scratch
        worktree whose contents are known to be disposable.
        """
        wt = self.create_worktree(branch_name, base_ref=base_ref)
        try:
            yield wt
        finally:
            if delete_on_exit and not self.remove_worktree(
                branch_name,
                force=True,
                discard_uncommitted=discard_uncommitted,
            ):
                raise RuntimeError("Managed worktree cleanup could not be verified")
