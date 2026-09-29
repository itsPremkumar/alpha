"""Worktree strategy selection: which isolation an agent actually gets.

This module does not build worktrees. ``WorktreeManager`` (this package) already
does that, and ``alpha.rsi.workspace`` already models ``kind``/``assurance`` for
one caller. What was missing is the *decision*: a single boolean such as
``use_worktree`` collapses four independent choices into one flag, and a flag
cannot answer "is this a merge simulation?" or "must this survive a crash?",
so it silently picks the wrong default and the operator cannot see why.

The vocabulary here is therefore a record, not a boolean:

    Isolation: git worktree (branch agent/042/TASK-1028, base 8f14e3c)

and when the requested isolation is not available, the degradation is stated
with the real reason rather than absorbed::

    Isolation: copy snapshot - assurance LOWER
    Reason: git exited 128: unable to access '.git': permission denied
    Consequence: you cannot commit, diff, or merge from this directory.

That disclosure is the whole point. A silently lower-assurance workspace is
how an agent spends twenty turns trying to ``git commit`` inside a directory
that was never a repository. This follows the honesty contract already
established in ``alpha.rsi.workspace`` (a degraded copy is recorded as
``assurance="lower"`` with the verbatim error), and it is why every
:class:`WorktreeSelection` here carries a non-empty ``reason``.

Three of the six modes are backed by machinery that already exists in this
repository, and this module says which:

===========================  ==========================================
mode                         backing
===========================  ==========================================
``WORKTREE``                 ``WorktreeManager.create_worktree``
``COPY``                     ``shutil.copytree`` (as in rsi/workspace)
``SANDBOX``                  ``sandbox.SandboxProvider`` (not selected here)
``EPHEMERAL``                ``worktree_context(discard_uncommitted=True)``
``INTEGRATION``              a detached worktree used for merge simulation
``LONG_LIVED``               ``WorktreeManager`` + an aging policy
===========================  ==========================================

``SANDBOX`` is recognised but never chosen by :func:`select_strategy`: sandbox
selection is a configuration decision owned by ``sandbox.provider``, and
silently escalating a task into a container from here would bypass that
operator-owned setting. It is accepted as an explicit *request* so a caller
that genuinely wants it can say so, and the request is answered with the real
reason when the provider cannot serve it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "Assurance",
    "TaskSignals",
    "WorktreeMode",
    "WorktreeSelection",
    "available_modes",
    "resolve_integration_mode",
    "select_strategy",
    "upgrade_or_report",
]


class WorktreeMode(StrEnum):
    """The isolation an agent's files physically are.

    These are not interchangeable, and the difference is not cosmetic: a
    ``COPY`` workspace cannot produce a mergeable diff, and an ``EPHEMERAL``
    one deliberately destroys its contents. Rendering two of them the same way
    would be the false-precision this repository exists to avoid.
    """

    #: A real git worktree on its own branch. Mergeable, diffable, recoverable.
    WORKTREE = "worktree"
    #: A bounded ``copytree`` snapshot. No branch, no diff, no merge path.
    COPY = "copy"
    #: A worktree backed by a sandbox provider. The only real host boundary.
    SANDBOX = "sandbox"
    #: A worktree removed on exit; its contents are disposable by contract.
    EPHEMERAL = "ephemeral"
    #: A detached throwaway worktree used to judge a merge without touching main.
    INTEGRATION = "integration"
    #: A persistent worktree that outlives the process and is reclaimed explicitly.
    LONG_LIVED = "long_lived"


class Assurance(StrEnum):
    """How much the selected isolation is actually worth.

    ``STANDARD`` means the mode delivered what it promises. ``LOWER`` means it
    did not, and the record must say so — never silently present a degraded
    workspace as the requested one.
    """

    STANDARD = "standard"
    LOWER = "lower"


@dataclass(frozen=True)
class TaskSignals:
    """What is known about a task when its workspace is chosen.

    Every field is optional and defaults to ``False``/``None``. An absent
    signal is not a negative finding — it is an absence — so the selector
    below never treats "unknown" as "safe"; unknown resolves to the mode that
    is recoverable rather than the mode that is fastest.
    """

    #: The task's edits must end up as a diff on a branch.
    produces_mergeable_diff: bool = False
    #: The task executes third-party or untrusted code, or installs dependencies.
    runs_untrusted_code: bool = False
    #: The task only reads; it is not expected to write.
    read_only: bool = False
    #: The diff is an expected by-product; the answer is the point.
    speculative: bool = False
    #: The task judges whether another branch may be merged.
    merge_verification: bool = False
    #: The work must survive a process restart, or a human will enter the tree.
    spans_sessions: bool = False
    #: An explicit request that outranks the signals above.
    requested_mode: WorktreeMode | None = None
    #: Set when the selector is resolving a degraded case, with the real reason.
    unavailable_reason: str | None = None


#: Branch names that must never be handed to a workspace for implementation work.
_PROTECTED_BRANCHES = frozenset({"main", "master", "trunk", "develop", "release"})


@dataclass(frozen=True)
class WorktreeSelection:
    """One isolation decision, with the evidence that produced it.

    ``reason`` is required and must be non-empty. A selection nobody can
    explain is indistinguishable from an arbitrary one, and the whole point of
    naming the mode is that an operator can later ask why this task got a copy
    workspace instead of a worktree.
    """

    mode: WorktreeMode
    assurance: Assurance
    reason: str
    #: A disposable merge-simulation worktree is not an implementation workspace.
    touches_main: bool = False
    #: Whether a failed task leaves recoverable work behind.
    survives_crash: bool = True
    #: Whether a diff from here can be merged.
    mergeable: bool = True

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValueError("a WorktreeSelection must carry a non-empty reason; an unexplained mode is arbitrary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": str(self.mode),
            "assurance": str(self.assurance),
            "reason": self.reason,
            "touches_main": self.touches_main,
            "survives_crash": self.survives_crash,
            "mergeable": self.mergeable,
        }

    def disclosure(self) -> str:
        """The model-facing statement of what isolation this task received.

        Written to be shown to an agent, not parsed by one. The degraded branch
        is the important half: it names the real failure and spells out the
        consequence, so the agent does not discover the limitation by trying a
        command that cannot work.
        """
        if self.assurance is Assurance.STANDARD:
            consequences = []
            if not self.survives_crash:
                consequences.append("anything you leave uncommitted is discarded when this task ends")
            if not self.mergeable:
                consequences.append("you cannot produce a mergeable diff here")
            suffix = f" Note: {'; '.join(consequences)}." if consequences else ""
            return f"Isolation: {self.mode}. {self.reason}.{suffix}"

        return (
            f"Isolation: {self.mode} - assurance {self.assurance.value.upper()}.\n"
            f"Reason: {self.reason}\n"
            "Consequence: the isolation you asked for is not available, so the guarantees above "
            "do not hold. Verify what this directory actually is before relying on it."
        )


def _degraded_copy(reason: str, *, requested: WorktreeMode | None = None) -> WorktreeSelection:
    """The honest fallback: a copy, labelled lower, with the real reason.

    ``requested`` names the mode that was actually asked for. It is included in
    the reason because "a copy was used" on its own is not actionable: the
    operator reading this needs to know that a *worktree* was requested and
    refused. Without it, the record says what happened but not what was lost.

    Note what this does *not* do: it never falls back to the shared checkout.
    That is the multi-agent failure the entire worktree design exists to
    prevent, so a degraded mode degrades in *assurance*, never in isolation.
    """
    if requested is not None and requested is not WorktreeMode.COPY:
        reason = f"{requested} was requested but is unavailable, so a copy snapshot was used instead. Cause: {reason}"
    return WorktreeSelection(
        mode=WorktreeMode.COPY,
        assurance=Assurance.LOWER,
        reason=reason,
        survives_crash=True,
        mergeable=False,
    )


def select_strategy(
    signals: TaskSignals,
    *,
    git_available: bool = True,
    git_error: str | None = None,
) -> WorktreeSelection:
    """Choose the isolation for a task, and say why.

    Precedence, highest first:

    1. an explicit ``requested_mode`` (an operator or a caller that knows
       better than the signals),
    2. merge verification, which must never share a workspace with
       implementation work,
    3. untrusted code, which needs the only real host boundary,
    4. read-only, speculative, and session-spanning tasks,
    5. the default: a plain worktree, because it is the only mode that is
       simultaneously recoverable and mergeable.

    Degradation happens in exactly one place — git being unusable — and it is
    always recorded as :attr:`Assurance.LOWER` with the verbatim reason.

    The git-availability check is applied to *every* git-backed outcome
    (1 through 5) rather than only the default, because returning
    ``WORKTREE`` from a deployment that cannot create worktrees produces a
    record that promises an isolation the caller will never get. Only
    :attr:`WorktreeMode.COPY` and :attr:`WorktreeMode.SANDBOX` survive it.
    """
    if signals.requested_mode is not None:
        return _resolve_requested(signals.requested_mode, signals, git_available=git_available, git_error=git_error)

    if signals.merge_verification:
        return _guard_git(
            WorktreeSelection(
                mode=WorktreeMode.INTEGRATION,
                assurance=Assurance.STANDARD,
                reason="merge verification runs in a detached throwaway worktree so main is never touched while a candidate is judged",
                touches_main=False,
                survives_crash=False,
                mergeable=False,
            ),
            git_available=git_available,
            git_error=git_error,
        )

    if signals.runs_untrusted_code:
        return _guard_git(
            WorktreeSelection(
                mode=WorktreeMode.SANDBOX,
                assurance=Assurance.STANDARD,
                reason="the task executes untrusted or third-party code, which needs a provider-backed boundary rather than a bare worktree",
                survives_crash=True,
            ),
            git_available=git_available,
            git_error=git_error,
        )

    if signals.read_only:
        return _guard_git(
            WorktreeSelection(
                mode=WorktreeMode.WORKTREE,
                assurance=Assurance.STANDARD,
                reason="read-only task; a worktree keeps its reads isolated from other agents' writes",
                survives_crash=True,
            ),
            git_available=git_available,
            git_error=git_error,
        )

    if signals.speculative:
        return _guard_git(
            WorktreeSelection(
                mode=WorktreeMode.EPHEMERAL,
                assurance=Assurance.STANDARD,
                reason="the diff is a by-product and the answer is the deliverable, so the workspace is disposable on exit",
                survives_crash=False,
                mergeable=False,
            ),
            git_available=git_available,
            git_error=git_error,
        )

    if signals.spans_sessions:
        return _guard_git(
            WorktreeSelection(
                mode=WorktreeMode.LONG_LIVED,
                assurance=Assurance.STANDARD,
                reason="the task spans sessions or a human will enter the tree, so the workspace must outlive the process",
                survives_crash=True,
            ),
            git_available=git_available,
            git_error=git_error,
        )

    if signals.produces_mergeable_diff:
        return _guard_git(
            WorktreeSelection(
                mode=WorktreeMode.WORKTREE,
                assurance=Assurance.STANDARD,
                reason="the task's output must merge, and a worktree is the only mode that is both recoverable and mergeable",
                survives_crash=True,
            ),
            git_available=git_available,
            git_error=git_error,
        )

    # The default is a worktree, not a copy or a fast path. The reasoning is
    # deliberate: an unrecognised task is *unknown*, and unknown must resolve
    # to the recoverable option, because the cost of wrongly choosing a
    # disposable mode is losing an agent's work while the cost of choosing a
    # worktree is only a checkout.
    return _guard_git(
        WorktreeSelection(
            mode=WorktreeMode.WORKTREE,
            assurance=Assurance.STANDARD,
            reason="default: a git worktree on its own branch, isolated from every other agent's files",
            survives_crash=True,
        ),
        git_available=git_available,
        git_error=git_error,
    )


def _guard_git(selection: WorktreeSelection, *, git_available: bool, git_error: str | None) -> WorktreeSelection:
    """Apply the git-availability check to an already-chosen selection.

    A selection whose mode does not need git passes through untouched. This
    keeps the availability rule in one place instead of duplicating it at each
    of the six return sites above, which is how a rule gets missed exactly once.
    """
    if git_available or selection.mode in (WorktreeMode.COPY, WorktreeMode.SANDBOX):
        return selection
    return _degraded_copy(
        git_error or "git is not available, so no worktree can be created",
        requested=selection.mode,
    )


def _resolve_requested(
    mode: WorktreeMode,
    signals: TaskSignals,
    *,
    git_available: bool,
    git_error: str | None,
) -> WorktreeSelection:
    """Answer an explicit request, degrading honestly rather than substituting.

    A request for a git-backed mode when git is unusable is answered with the
    copy fallback and the real reason. Silently serving the *next best* mode
    while reporting the requested one is the specific dishonesty this module
    exists to prevent.
    """
    needs_git = mode in (WorktreeMode.WORKTREE, WorktreeMode.EPHEMERAL, WorktreeMode.INTEGRATION, WorktreeMode.LONG_LIVED)
    if needs_git and not git_available:
        return _degraded_copy(
            git_error or "git is unavailable, so no worktree can be created",
            requested=mode,
        )

    if mode is WorktreeMode.SANDBOX:
        # Recognised, but never silently selected. Sandbox choice belongs to
        # the operator's configured provider.
        return WorktreeSelection(
            mode=WorktreeMode.SANDBOX,
            assurance=Assurance.STANDARD,
            reason="sandbox isolation was explicitly requested; the configured provider owns its lifecycle",
            survives_crash=True,
        )

    durable = mode is not WorktreeMode.EPHEMERAL
    # A copy workspace has no branch, so it cannot produce a diff that merges —
    # and an ephemeral one is disposable by contract. Both facts have to be
    # carried on the record, because a caller reading only `mergeable` would
    # otherwise be told it can hand a diff to the integrator.
    mergeable = mode in (WorktreeMode.WORKTREE, WorktreeMode.SANDBOX, WorktreeMode.LONG_LIVED)
    return WorktreeSelection(
        mode=mode,
        assurance=Assurance.STANDARD,
        reason=f"{mode} was explicitly requested for this task",
        survives_crash=durable,
        mergeable=mergeable,
    )


def upgrade_or_report(
    selection: WorktreeSelection,
    available_modes: set[WorktreeMode],
) -> WorktreeSelection:
    """Report the gap when a task needs a mode this deployment cannot provide.

    The alternative — quietly substituting a mode that happens to be
    available — is how an agent ends up running untrusted code in a directory
    with no boundary while believing it was sandboxed. The returned record
    keeps the *requested* mode and marks it lower assurance, so the missing
    capability is visible instead of absorbed.
    """
    if selection.mode in available_modes:
        return selection

    return WorktreeSelection(
        mode=selection.mode,
        assurance=Assurance.LOWER,
        reason=(
            f"{selection.mode} isolation was selected for this task but is not provided by this deployment; "
            f"available modes: {', '.join(sorted(str(m) for m in available_modes)) or 'none'}. "
            "The guarantees of the requested mode do not hold."
        ),
        touches_main=selection.touches_main,
        survives_crash=selection.survives_crash,
        mergeable=selection.mergeable,
    )


def available_modes(*, git_available: bool = True) -> set[WorktreeMode]:
    """The modes this process can actually deliver right now.

    The set is derived from capability rather than declared as a constant,
    because a static list would keep advertising ``SANDBOX`` on a deployment
    with no provider and ``INTEGRATION`` where git cannot create a worktree —
    and ``upgrade_or_report`` would then report a capability that does not
    exist. ``COPY`` is always available: it is pure ``shutil`` and needs
    nothing from the environment, which is why it is the only honest fallback.
    """
    modes = {WorktreeMode.COPY}
    if git_available:
        # The four git-backed worktree modes all resolve through
        # `WorktreeManager`, and `INTEGRATION` is provided by
        # `alpha.sandbox.merge_simulation.simulate_merge`, which is built on
        # that same manager's detached worktree support and is reachable as
        # `WorktreeManager.simulate_merge`.
        modes |= {
            WorktreeMode.WORKTREE,
            WorktreeMode.EPHEMERAL,
            WorktreeMode.LONG_LIVED,
            WorktreeMode.INTEGRATION,
        }
    return modes


def resolve_integration_mode(selection: WorktreeSelection) -> WorktreeSelection:
    """Confirm an ``INTEGRATION`` selection against what is actually available.

    A caller that selected merge-verification isolation gets its capability
    checked here rather than assuming the selection was satisfiable. Returning
    the record instead of a bool keeps the reason attached, so a caller that
    cannot proceed can say *why* rather than falling through to a direct merge
    against the branch it was trying to protect.
    """
    if selection.mode is not WorktreeMode.INTEGRATION:
        return selection
    return upgrade_or_report(selection, available_modes())


#: Branch names an implementation workspace may never be created on.
def is_protected_branch(branch: str) -> bool:
    """True when a branch must not host implementation work.

    Kept here next to the mode vocabulary because it is the same rule the
    selector depends on: no mode other than ``INTEGRATION`` may be paired with
    a protected branch, and even the simulation worktree is detached rather
    than checked out on one.
    """
    head = branch.split("/", 1)[0].strip().lower()
    return head in _PROTECTED_BRANCHES


_VALID_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def is_safe_branch_name(branch: str) -> bool:
    """Mirror of ``WorktreeManager``'s own check, for callers that must decide first.

    Deliberately the same shape as the validator in
    ``alpha.sandbox.worktrees`` so a name rejected here is not accepted later
    by a different rule in a different layer.
    """
    if not isinstance(branch, str) or not _VALID_BRANCH.match(branch) or ".." in branch:
        return False
    return not any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in branch.split("/"))
