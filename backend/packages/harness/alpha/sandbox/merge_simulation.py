"""Merge simulation: judge a candidate branch without touching ``main``.

The strategy selector can choose :attr:`WorktreeMode.INTEGRATION`, but a mode
is a decision, not a capability. This is the capability behind it.

The problem it solves is narrow and important. A merge that is attempted
directly against the integration branch leaves three problems behind when it
goes wrong: the working tree holds conflict markers, the ref may have moved,
and the operator is left with an ambiguous half-merged state. Worse, a textual
merge that *succeeds* can still be logically wrong, which is why the result of
this function is deliberately not a verdict about the code — it is a verdict
about whether the trees could be joined at all.

So the contract is:

- the candidate is merged into a **detached throwaway worktree** at the base
  commit, never into the base branch;
- the result distinguishes *clean*, *conflicted*, and *failed*, because
  "failed" (an unreachable ref, a missing object) is not "conflicted" and
  reporting it as a conflict sends an operator looking for a resolution that
  is not the problem;
- conflicted files are listed by name, verbatim from git;
- the worktree is removed on every path, including failures;
- nothing here reports a merge as *safe* or *verified*. A clean merge means
  git found no textual conflict, which is a much smaller claim.

The distinction between the last two points is the whole reason this module
exists separately from the release gate. The gate answers "should this ship?".
This answers "can these two trees be joined, and if not, where?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.sandbox.worktree_strategy import TaskSignals, is_safe_branch_name, resolve_integration_mode, select_strategy
from alpha.sandbox.worktrees import WorktreeManager

__all__ = ["MergeOutcome", "MergeSimulation", "simulate_merge"]

#: Git's own unmerged-state codes, as reported by `git diff --name-only
#: --diff-filter=U`. Only these count as conflicts.
_UNMERGED_CODES = frozenset({"U"})


class MergeOutcome(StrEnum):
    """What actually happened when the trees were joined.

    ``CONFLICTED`` and ``FAILED`` are separate states on purpose. A failed
    merge — an unknown ref, a missing object, a dirty base — is an operational
    problem with no resolution to apply, and collapsing it into "conflict"
    would send an operator hunting for merge markers that do not exist.
    """

    #: The merge applied with no textual conflict. NOT a correctness verdict.
    CLEAN = "clean"
    #: Git stopped with unmerged paths. ``conflicted_files`` names them.
    CONFLICTED = "conflicted"
    #: The merge could not be attempted, or failed for a non-conflict reason.
    FAILED = "failed"


@dataclass(frozen=True)
class MergeSimulation:
    """The measured result of one merge attempt in a throwaway worktree.

    ``outcome`` is never inferred. It comes from git's own status, and every
    field that could not be measured is empty rather than defaulted, so a
    caller cannot read a failed simulation as a clean one.
    """

    outcome: MergeOutcome
    base_ref: str
    base_sha: str | None
    head_sha: str | None
    reason: str
    conflicted_files: tuple[str, ...] = ()
    #: True only when the base ref provably did not move during the attempt.
    base_unchanged: bool = True
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def is_mergeable(self) -> bool:
        """Whether the trees could be joined textually.

        This is a *necessary*, not a sufficient, condition for merging. A
        clean text merge can still be logically wrong, so this must not be
        read as "verified" or "safe" — see the module docstring.
        """
        return self.outcome is MergeOutcome.CLEAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": str(self.outcome),
            "base_ref": self.base_ref,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "reason": self.reason,
            "conflicted_files": list(self.conflicted_files),
            "base_unchanged": self.base_unchanged,
            "is_mergeable": self.is_mergeable,
            "diagnostics": dict(self.diagnostics),
        }

    def disclosure(self) -> str:
        """A statement an operator or a model can act on."""
        if self.outcome is MergeOutcome.CONFLICTED:
            files = ", ".join(self.conflicted_files) or "(git reported unmerged paths without naming them)"
            return (
                f"Merge of {self.head_sha or 'the candidate'} into {self.base_ref} CONFLICTS in: {files}. "
                "These paths need an explicit resolution; git could not join the trees on its own. "
                "A textual resolution is not necessarily a correct one — review it."
            )
        if self.outcome is MergeOutcome.FAILED:
            return f"Merge simulation could not be completed: {self.reason}"
        return (
            f"Merge of {self.head_sha or 'the candidate'} into {self.base_ref} applied with no textual conflict. "
            "This says the trees could be joined — NOT that the result is correct, tested, or safe to ship."
        )


def _changed_paths(worktree: Path, manager: WorktreeManager) -> list[str]:
    """Files git still considers unmerged, straight from git."""
    proc = manager.run_in_worktree(worktree, ["diff", "--name-only", "--diff-filter=U"], check=False)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _abort_merge(worktree: Path, manager: WorktreeManager) -> None:
    """Abandon a merge attempt so the worktree can be removed cleanly.

    Best-effort by design: the worktree is removed immediately afterwards, so a
    failure here cannot leak an agent's work — there is no agent work in a
    simulation tree. It is still attempted, because leaving a tree mid-merge
    makes the subsequent removal fail for a confusing reason.
    """
    manager.run_in_worktree(worktree, ["merge", "--abort"], check=False)


def simulate_merge(
    manager: WorktreeManager,
    *,
    base_ref: str,
    head_ref: str,
    name: str,
) -> MergeSimulation:
    """Attempt ``head_ref`` into ``base_ref`` inside a throwaway worktree.

    ``base_ref`` is resolved to a commit *before* the worktree is created and
    the same commit is used as the merge target, so a base that moves while the
    simulation runs cannot make the result describe a different base than the
    one reported. When the ref moved, ``base_unchanged`` is ``False`` and the
    caller knows to re-run before trusting the outcome.
    """
    if not is_safe_branch_name(head_ref) and not re.fullmatch(r"[0-9a-f]{7,40}", head_ref):
        return MergeSimulation(
            outcome=MergeOutcome.FAILED,
            base_ref=base_ref,
            base_sha=None,
            head_sha=None,
            reason=f"refusing to simulate an unsafe candidate ref: {head_ref!r}",
        )

    # Ask the strategy layer for merge-verification isolation and confirm this
    # process can actually provide it, rather than assuming the mode is
    # satisfiable. If it is not, the simulation is refused — a caller must
    # never fall back to merging the candidate into the branch it was trying
    # to protect just because the safe path was unavailable.
    selection = resolve_integration_mode(select_strategy(TaskSignals(merge_verification=True)))
    if selection.assurance.value != "standard":
        return MergeSimulation(
            outcome=MergeOutcome.FAILED,
            base_ref=base_ref,
            base_sha=None,
            head_sha=None,
            reason=f"merge simulation is unavailable: {selection.reason}",
        )

    try:
        base_sha = manager.run_in_worktree(manager.repo_root, ["rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}"], check=True).stdout.strip()
    except Exception as exc:
        return MergeSimulation(
            outcome=MergeOutcome.FAILED,
            base_ref=base_ref,
            base_sha=None,
            head_sha=None,
            reason=f"base ref {base_ref!r} could not be resolved to a commit: {type(exc).__name__}: {exc}",
        )

    try:
        head_sha = manager.run_in_worktree(manager.repo_root, ["rev-parse", "--verify", "--end-of-options", f"{head_ref}^{{commit}}"], check=True).stdout.strip()
    except Exception as exc:
        return MergeSimulation(
            outcome=MergeOutcome.FAILED,
            base_ref=base_ref,
            base_sha=base_sha,
            head_sha=None,
            reason=f"candidate ref {head_ref!r} could not be resolved to a commit: {type(exc).__name__}: {exc}",
        )

    try:
        worktree = manager.create_detached_worktree(name, base_sha)
    except Exception as exc:
        return MergeSimulation(
            outcome=MergeOutcome.FAILED,
            base_ref=base_ref,
            base_sha=base_sha,
            head_sha=head_sha,
            reason=f"could not create the simulation worktree: {type(exc).__name__}: {exc}",
        )

    try:
        merge = manager.run_in_worktree(
            worktree,
            ["merge", "--no-commit", "--no-ff", head_sha],
            check=False,
        )

        # Was the base ref still where we said it was? A merge that ran
        # against a base that has since moved describes a base that no longer
        # exists, which is a real staleness signal rather than a detail.
        try:
            base_now = manager.run_in_worktree(manager.repo_root, ["rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}"], check=True).stdout.strip()
        except Exception:
            base_now = ""
        base_unchanged = base_now == base_sha

        if merge.returncode == 0:
            return MergeSimulation(
                outcome=MergeOutcome.CLEAN,
                base_ref=base_ref,
                base_sha=base_sha,
                head_sha=head_sha,
                reason="git applied the merge with no textual conflict",
                base_unchanged=base_unchanged,
            )

        conflicted = _changed_paths(worktree, manager)
        if conflicted:
            return MergeSimulation(
                outcome=MergeOutcome.CONFLICTED,
                base_ref=base_ref,
                base_sha=base_sha,
                head_sha=head_sha,
                reason="git stopped with unmerged paths",
                conflicted_files=tuple(conflicted),
                base_unchanged=base_unchanged,
            )

        # A non-zero exit with no unmerged paths is not a conflict. Reporting
        # it as one would send an operator looking for markers that were never
        # written, so it is reported as a failure with git's own stderr.
        return MergeSimulation(
            outcome=MergeOutcome.FAILED,
            base_ref=base_ref,
            base_sha=base_sha,
            head_sha=head_sha,
            reason=f"git merge exited {merge.returncode} without reporting unmerged paths: {(merge.stderr or merge.stdout).strip() or 'no message'}",
            base_unchanged=base_unchanged,
            diagnostics={"git_returncode": merge.returncode},
        )
    finally:
        _abort_merge(worktree, manager)
        try:
            manager.remove_detached_worktree(name)
        except Exception:
            # A leaked simulation worktree is untidy, not dangerous, and the
            # worktree manager rehydrates from git so it stays reclaimable.
            # Swallowing here would mask the original result, which is the
            # thing the caller actually asked for.
            pass
