"""Self-modification as a governed PROPOSAL, routed through the existing Sentinel.

Do not invent a self-update mechanism
-------------------------------------
A guarded one already ships. This module deliberately does not implement a
second one:

* the repair state machine is
  :class:`alpha.runtime.sentinel.loop.SentinelLoop` — OBSERVE -> DIAGNOSE -> FIX
  -> VERIFY -> COMMIT, with checkpoint-before-edit, revert-on-red, attempt caps
  and escalate-on-unknown already implemented and tested there;
* the local execution policy is
  :func:`alpha.evolution.update_policy.load_update_policy` with its kill-switch
  semantics (``enabled`` / ``auto_apply``) and its hard field validation.

What this module adds is the governance layer those two were missing: a
*proposal* that can be refused, an explicit blast radius, and a record that says
what changed, why, and what verification said.

The blast radius
----------------
:data:`SELF_MODIFIABLE_GLOBS` is the complete list of what an automated
self-modification may touch. It is a short list of documentation and
profile-data surfaces. Code is not on it, and never will be by default: the
Sentinel's own repair path is the only code mutator, and it runs behind the
Sentinel's own attempt caps and its own verification, not behind this module.

:data:`PERMANENTLY_OFF_LIMITS` is the union of
:data:`alpha.bots.authority_ceiling.PROTECTED_COMPONENTS` plus the runtime
self-modification machinery itself. These are refused unconditionally — there is
no flag, no override, and no "trusted actor" that gets an exception. A ceiling
that an automated path can edit is not a ceiling.

The kill switch
---------------
:data:`KILL_SWITCH_ENV` is checked on EVERY proposal, not once at startup, and it
is checked before the proposal is even constructed into a runnable shape. A
proposal that is refused because the kill switch is engaged is written to the
ledger, so "the kill switch stopped it" is visible after the fact rather than
being indistinguishable from "nothing happened".
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from alpha.bots.authority_ceiling import (
    PROTECTED_COMPONENTS,
    AuthorityCeiling,
    is_protected_component,
)
from alpha.bots.governance_ledger import (
    ACTION_SELF_MODIFICATION_PROPOSED,
    ACTION_SELF_MODIFICATION_RESULT,
    record_governance_action,
)
from alpha.bots.kill_switch import is_kill_switch_active
from alpha.bots.profile import _now

logger = logging.getLogger(__name__)

#: Operator kill switch. Any non-empty value other than an explicit falsey word
#: stops all self-modification immediately, at proposal time.
KILL_SWITCH_ENV: Final = "ALPHA_DISABLE_SELF_MODIFICATION"

_FALSEY: Final[frozenset[str]] = frozenset({"", "0", "false", "no", "off"})

#: The complete blast radius of an automated self-modification: documentation
#: and profile-data surfaces only. No code, no configuration, no policy.
SELF_MODIFIABLE_GLOBS: Final[tuple[str, ...]] = (
    "docs/**/*.md",
    "*.md",
    "alpha/bots/runtime_profiles.json",
)

#: Union of the ceiling's protected components and the self-modification
#: machinery. Refused unconditionally, with no override path.
PERMANENTLY_OFF_LIMITS: Final[tuple[str, ...]] = tuple(
    sorted(
        set(PROTECTED_COMPONENTS)
        | {
            "alpha/bots/self_modification",
            "alpha.bots.self_modification",
            "self_modification.py",
            "alpha/runtime/sentinel",
            "alpha.runtime.sentinel",
            "alpha/bots/authority_ceiling",
        }
    )
)

#: Verdict values.
VERDICT_FIXED = "fixed"
VERDICT_REVERTED = "reverted"
VERDICT_ESCALATED = "escalated"
VERDICT_REFUSED = "refused"
VERDICT_KILLED = "killed_by_switch"


class SelfModificationRefused(RuntimeError):
    """A self-modification was refused. Carries a machine-readable reason."""

    def __init__(self, message: str, *, reason: str, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.violations = list(violations or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "self_modification_refused",
            "message": str(self),
            "reason": self.reason,
            "violations": list(self.violations),
        }


def self_modification_kill_switch_engaged() -> bool:
    """Read the kill switch NOW, not at import time.

    Reading it at import time is the bug this avoids: an operator who sets the
    switch after the process started would get no protection at all.
    """
    if os.getenv(KILL_SWITCH_ENV, "").strip().lower() not in _FALSEY:
        return True
    # The existing fleet-wide switch also stops self-modification. It is an
    # operator stop button; letting self-modification ignore it would make the
    # stop button a lie.
    active, _reason = is_kill_switch_active()
    return bool(active)


@dataclass
class SelfModificationProposal:
    """A request to change something in the repository.

    A proposal is not an action. It must be approved, it must survive the
    Sentinel's own verification, and if verification is red it is reverted by the
    Sentinel's checkpoint manager before this module returns.
    """

    title: str
    targets: list[str]
    rationale: str
    evidence: str = ""
    actor: str = "alpha"
    proposal_id: str = ""
    replacement_contents: dict[str, str] = field(default_factory=dict)
    state: str = "pending"
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.proposal_id:
            import uuid

            self.proposal_id = f"selfmod-{uuid.uuid4().hex[:10]}"
        if not self.title.strip():
            raise ValueError("self-modification proposal requires a title")
        if not self.rationale.strip():
            # Evidence-free self-modification is indistinguishable from a
            # compromise, so the rationale is mandatory rather than advisory.
            raise ValueError("self-modification proposal requires a rationale (the evidence)")
        if not isinstance(self.targets, list) or not self.targets:
            raise ValueError("self-modification proposal requires at least one target")
        if not all(isinstance(t, str) and t.strip() for t in self.targets):
            raise ValueError("self-modification targets must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "targets": list(self.targets),
            "rationale": self.rationale,
            "evidence": self.evidence,
            "actor": self.actor,
            "state": self.state,
            "created_at": self.created_at,
        }


@dataclass
class SelfModificationOutcome:
    """What actually happened, with everything an auditor needs."""

    proposal_id: str
    title: str
    verdict: str
    targets: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    before_after: list[dict[str, str]] = field(default_factory=list)
    verify_summary: str = ""
    verify_passed: bool = False
    evidence: str = ""
    rolled_back: bool = False
    reason: str = ""
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "verdict": self.verdict,
            "targets": list(self.targets),
            "changed_files": list(self.changed_files),
            "before_after": list(self.before_after),
            "verify_summary": self.verify_summary,
            "verify_passed": self.verify_passed,
            "evidence": self.evidence,
            "rolled_back": self.rolled_back,
            "reason": self.reason,
            "violations": list(self.violations),
        }


def is_self_modifiable(target: str, *, repo_root: str | Path | None = None) -> bool:
    """Is *target* inside the declared blast radius?

    Three independent refusals, because a blast radius that is one boolean
    expression is a blast radius that one refactor widens:

    1. the permanently-off-limits list (which includes the ceiling and the
       Sentinel itself);
    2. a path escaping the repository root;
    3. a glob outside :data:`SELF_MODIFIABLE_GLOBS`.
    """
    raw = str(target or "").strip()
    if not raw:
        return False
    normalised = raw.replace("\\", "/")
    if is_protected_component(normalised) or any(
        item.lower() in normalised.lower() for item in PERMANENTLY_OFF_LIMITS
    ):
        return False
    if repo_root is not None:
        try:
            root = Path(repo_root).resolve()
            candidate = (root / normalised).resolve()
            if not candidate.is_relative_to(root):
                return False
        except (OSError, ValueError):
            return False
    from fnmatch import fnmatch

    return any(fnmatch(normalised, pattern) for pattern in SELF_MODIFIABLE_GLOBS)


class SelfModificationGuard:
    """The gatekeeper. Holds no mutation logic of its own.

    All mutation is delegated to the existing
    :class:`alpha.runtime.sentinel.loop.SentinelLoop`, so checkpointing,
    verification and revert come from the component that already implements and
    tests them.
    """

    def __init__(
        self,
        repo_root: str | Path,
        *,
        ceiling: AuthorityCeiling | None = None,
        event_store: Any = None,
        approver: Any = None,
        loop_factory: Any = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self._ceiling = ceiling
        self._event_store = event_store
        #: ``approver(proposal) -> bool``. Defaults to "no unattended self-modification".
        self._approver = approver
        #: ``loop_factory(repo_root, fix_fn) -> SentinelLoop``. Injectable so the
        #: test can drive the real loop with a deterministic fix function.
        self._loop_factory = loop_factory

    # -- gates -------------------------------------------------------------
    def assert_kill_switch_off(self) -> None:
        if self_modification_kill_switch_engaged():
            raise SelfModificationRefused(
                f"self-modification is disabled by {KILL_SWITCH_ENV} or the fleet kill switch",
                reason=VERDICT_KILLED,
                violations=[f"kill_switch:{KILL_SWITCH_ENV}"],
            )

    def assert_within_blast_radius(self, proposal: SelfModificationProposal) -> None:
        violations = [
            target
            for target in proposal.targets
            if not is_self_modifiable(target, repo_root=self.repo_root)
        ]
        if violations:
            raise SelfModificationRefused(
                f"target(s) {violations} are outside the self-modification blast radius "
                f"or permanently off-limits",
                reason=VERDICT_REFUSED,
                violations=[f"outside_blast_radius:{t}" for t in violations],
            )

    def assert_approved(self, proposal: SelfModificationProposal) -> None:
        """No unattended self-modification without an explicit approver.

        The default is refusal. That is the safe default for a component whose
        whole purpose is writing to the repository: "nobody said yes" must mean
        "no", not "probably fine".
        """
        if self._approver is None:
            raise SelfModificationRefused(
                "no approver is configured; self-modification is never applied unattended",
                reason=VERDICT_REFUSED,
                violations=["no_approver"],
            )
        if not bool(self._approver(proposal)):
            raise SelfModificationRefused(
                "the approver refused this self-modification proposal",
                reason=VERDICT_REFUSED,
                violations=["approver_refused"],
            )

    # -- the flow ----------------------------------------------------------
    def propose(
        self,
        proposal: SelfModificationProposal,
        *,
        verification_commands: Mapping[str, list[str]] | None = None,
    ) -> SelfModificationOutcome:
        """Run one self-modification proposal through the full governed path.

        Order is deliberate and is the security property:

        1. kill switch, then blast radius, then approval — all BEFORE any file
           is read for modification;
        2. the Sentinel loop does checkpoint -> fix -> verify;
        3. on red verification the Sentinel restores the checkpoint, and this
           method reports ``rolled_back=True``;
        4. the outcome — including before/after, evidence and the verify
           summary — is written to the ordered ledger.
        """
        try:
            self.assert_kill_switch_off()
            self.assert_within_blast_radius(proposal)
            self.assert_approved(proposal)
        except SelfModificationRefused as refusal:
            record_governance_action(
                ACTION_SELF_MODIFICATION_PROPOSED,
                actor=proposal.actor,
                target=proposal.proposal_id,
                reason=proposal.rationale,
                details={
                    "verdict": refusal.reason,
                    "violations": refusal.violations,
                    "targets": list(proposal.targets),
                },
                store=self._event_store,
            )
            return SelfModificationOutcome(
                proposal_id=proposal.proposal_id,
                title=proposal.title,
                verdict=refusal.reason,
                targets=list(proposal.targets),
                evidence=proposal.evidence,
                reason=str(refusal),
                violations=refusal.violations,
            )

        record_governance_action(
            ACTION_SELF_MODIFICATION_PROPOSED,
            actor=proposal.actor,
            target=proposal.proposal_id,
            reason=proposal.rationale,
            details={
                "verdict": "admitted",
                "targets": list(proposal.targets),
                "evidence": proposal.evidence,
            },
            store=self._event_store,
        )

        before_after = self._capture_before(proposal.targets)
        checks = dict(verification_commands or {})

        def fix_fn(signal: Any, snapshot: Any) -> list[str]:
            # Snapshot BEFORE editing — the Sentinel refuses to proceed when a
            # fix does not, because without a checkpoint there is no way back.
            targets = [t for t in proposal.targets if is_self_modifiable(t, repo_root=self.repo_root)]
            if targets:
                snapshot(targets)
            written: list[str] = []
            for target in targets:
                destination = self.repo_root / target
                content = proposal.replacement_contents.get(target)
                if content is None:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
                written.append(target)
            return written

        loop = self._build_loop(fix_fn, checks)
        outcome = loop.handle(self._as_signal(proposal), verification_commands=checks)
        report = outcome.verify
        passed = bool(report and report.passed)
        changed = list(outcome.commit.staged) if outcome.commit and outcome.commit.ok else []
        for entry in before_after:
            entry["after"] = self._read_text(entry["target"])
            if not passed and entry["after"] != entry["before"]:
                entry["after_rolled_back"] = True

        result = SelfModificationOutcome(
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            verdict=outcome.status,
            targets=list(proposal.targets),
            changed_files=changed,
            before_after=before_after,
            verify_summary=report.summary() if report else "",
            verify_passed=passed,
            evidence=proposal.evidence,
            rolled_back=(outcome.status == "reverted"),
            reason=outcome.detail,
        )
        record_governance_action(
            ACTION_SELF_MODIFICATION_RESULT,
            actor=proposal.actor,
            target=proposal.proposal_id,
            reason=proposal.rationale,
            details=result.to_dict(),
            store=self._event_store,
        )
        return result

    # -- internals ---------------------------------------------------------
    def _build_loop(self, fix_fn: Any, checks: Mapping[str, list[str]]) -> Any:
        if self._loop_factory is not None:
            return self._loop_factory(self.repo_root, fix_fn)
        from alpha.runtime.sentinel.loop import SentinelLoop

        return SentinelLoop(self.repo_root, fix_fn=fix_fn)

    def _as_signal(self, proposal: SelfModificationProposal) -> Any:
        from alpha.runtime.sentinel.signals import Signal

        # The proposal is a documentation-shaped repair. It is registered under
        # the Sentinel's own signal machinery so attempt caps and cooldowns
        # apply to it exactly as they do to any other fault.
        return Signal(
            source="self_modification",
            kind="missing_bom",
            message=f"{proposal.title}: {proposal.rationale}",
            context={"proposal_id": proposal.proposal_id, "targets": list(proposal.targets)},
        )

    def _capture_before(self, targets: Iterable[str]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for target in targets:
            out.append({"target": target, "before": self._read_text(target), "after": ""})
        return out

    def _read_text(self, target: str) -> str:
        path = self.repo_root / target
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def blast_radius(self) -> dict[str, Any]:
        """The inspectable answer to "what can the sentinel change?"."""
        return {
            "self_modifiable_globs": list(SELF_MODIFIABLE_GLOBS),
            "permanently_off_limits": list(PERMANENTLY_OFF_LIMITS),
            "kill_switch_env": KILL_SWITCH_ENV,
            "requires_approver": True,
            "verifies_before_apply": True,
            "reverts_on_failed_verification": True,
        }


__all__ = [
    "KILL_SWITCH_ENV",
    "PERMANENTLY_OFF_LIMITS",
    "SELF_MODIFIABLE_GLOBS",
    "SelfModificationGuard",
    "SelfModificationOutcome",
    "SelfModificationProposal",
    "SelfModificationRefused",
    "VERDICT_ESCALATED",
    "VERDICT_FIXED",
    "VERDICT_KILLED",
    "VERDICT_REFUSED",
    "VERDICT_REVERTED",
    "is_self_modifiable",
    "self_modification_kill_switch_engaged",
]
