"""Self-Repo Mutation Guard protecting the active agent runtime checkout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SelfRepoViolation:
    is_violation: bool
    reason: str = ""
    command: str = ""


class SelfRepoGuard:
    """Detects Git operations that can alter or destroy the running harness checkout."""

    MUTATING_GIT_ACTIONS = frozenset({
        "checkout", "switch", "rebase", "merge", "pull",
        "restore", "clean", "cherry-pick", "revert", "reset",
    })

    def __init__(self, running_repo_root: Path | str | None = None, allow_self_mutation: bool = False):
        if running_repo_root is None:
            # Running repo root of alpha
            running_repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        self.running_repo_root = Path(running_repo_root).resolve()
        self.allow_self_mutation = allow_self_mutation

    def evaluate_command(self, command: str, target_cwd: Path | str | None = None) -> SelfRepoViolation:
        """Evaluate if a shell command attempts to mutate the active harness checkout."""
        if self.allow_self_mutation:
            return SelfRepoViolation(is_violation=False, command=command)

        cmd = command.strip()
        effective_cwd = Path(target_cwd or Path.cwd()).resolve()

        # Check if targeting running repo root or subdirectory within it (and NOT in a .worktree)
        is_targeting_self = False
        try:
            # If cwd is inside running_repo_root
            if effective_cwd == self.running_repo_root or self.running_repo_root in effective_cwd.parents:
                # Except if inside an isolated worktree folder (.worktrees)
                if ".worktrees" not in effective_cwd.parts:
                    is_targeting_self = True
        except Exception:
            pass

        # Check -C or --work-tree flags
        c_match = re.search(r"git\s+(?:-C|--work-tree)\s+([^\s]+)", cmd)
        if c_match:
            flag_path = Path(c_match.group(1)).resolve()
            if flag_path == self.running_repo_root or self.running_repo_root in flag_path.parents:
                if ".worktrees" not in flag_path.parts:
                    is_targeting_self = True
            else:
                # Explicitly targeting some other path outside running repo
                is_targeting_self = False

        if not is_targeting_self:
            return SelfRepoViolation(is_violation=False, command=command)

        # Check for mutating git subcommands
        git_match = re.search(r"\bgit\s+(?:-[^\s]+\s+)*([a-z\-]+)", cmd, re.IGNORECASE)
        if git_match:
            subcmd = git_match.group(1).lower()
            if subcmd in self.MUTATING_GIT_ACTIONS:
                # For reset, only hard/merge/keep are dangerous
                if subcmd == "reset" and not re.search(r"--(hard|merge|keep)", cmd):
                    return SelfRepoViolation(is_violation=False, command=command)

                return SelfRepoViolation(
                    is_violation=True,
                    reason=f"Self-repo mutation blocked: '{cmd}' would modify the active harness runtime directory.",
                    command=command,
                )

        return SelfRepoViolation(is_violation=False, command=command)


_global_repo_guard = SelfRepoGuard()


def get_self_repo_guard() -> SelfRepoGuard:
    return _global_repo_guard
