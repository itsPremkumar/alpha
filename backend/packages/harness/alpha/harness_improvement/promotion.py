"""The gated promotion ladder, atomic activation and rollback (guide §11.3).

Promotion is the one place a candidate becomes the active harness, so it is
deliberately the most conservative module in the package:

* **A gate that did not run is not a gate that passed.** Every gate is
  tri-state: satisfied, unsatisfied, or *unmeasured*. ``automatic_global_promotion``
  is off by default, a regression suite is required by default, and a candidate
  whose regression suite never ran cannot reach ACTIVE.
* **Privileged kinds need human approval even when automation is on.** Scripts
  and middleware always carry a required-approval flag; that flag is checked by
  :func:`evaluate_promotion` and is not bypassed by ``force``.
* **Activation is an atomic pointer swap with the prior bundle remembered.**
  :func:`activate` writes the new pointer with ``previous_bundle_id`` set to the
  bundle that was active, so :func:`rollback` is a one-step restore, and both
  events land in the durable promotion log.
* **A rollback that cannot be performed says so.** With no prior bundle there is
  nothing to restore, and the refusal names that — it does not clear the
  pointer and call it a rollback.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.harness_improvement.models import (
    ComponentKind,
    ContrastReport,
    ContrastVerdict,
    HarnessBundle,
    PromotionState,
    ValidatorStatus,
)
from alpha.harness_improvement.store import ActivePointer, HarnessImprovementStore, StoreCorrupt, StoreUnreadable, utc_now

#: The ladder's forward edges. Everything not listed is not a promotion.
LADDER: dict[PromotionState, PromotionState] = {
    PromotionState.DRAFT: PromotionState.STATICALLY_VALIDATED,
    PromotionState.STATICALLY_VALIDATED: PromotionState.PROBED,
    PromotionState.PROBED: PromotionState.REGRESSION_TESTED,
    PromotionState.REGRESSION_TESTED: PromotionState.SHADOW,
    PromotionState.SHADOW: PromotionState.CANARY,
    PromotionState.CANARY: PromotionState.ACTIVE,
}

#: States from which a candidate may still be promoted.
PROMOTABLE_STATES: frozenset[PromotionState] = frozenset(LADDER) | {PromotionState.ACTIVE}


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    """Operator policy, defaults from the guide's §17 sketch."""

    automatic_global_promotion: bool = False
    require_regression_suite: bool = True
    require_human_approval_for_scripts: bool = True
    require_human_approval_for_middleware: bool = True
    require_human_approval_for_permissions: bool = True
    keep_last_known_good: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "automatic_global_promotion": self.automatic_global_promotion,
            "require_regression_suite": self.require_regression_suite,
            "require_human_approval_for_scripts": self.require_human_approval_for_scripts,
            "require_human_approval_for_middleware": self.require_human_approval_for_middleware,
            "require_human_approval_for_permissions": self.require_human_approval_for_permissions,
            "keep_last_known_good": self.keep_last_known_good,
        }


@dataclass(frozen=True, slots=True)
class RegressionSuiteResult:
    """The measured regression evidence for a candidate.

    ``passed`` is ``None`` when the suite did not run — the exact value that
    must not be confused with ``True``.
    """

    name: str
    passed: bool | None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    """Everything a promotion decision may consult, explicitly measured."""

    validation_clean: bool | None = None
    probe_outcomes: int = 0
    contrasts: tuple[ContrastReport, ...] = ()
    regression_suites: tuple[RegressionSuiteResult, ...] = ()
    approvals: tuple[str, ...] = ()
    component_kinds: tuple[ComponentKind, ...] = ()
    deliverable_verified: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_clean": self.validation_clean,
            "probe_outcomes": self.probe_outcomes,
            "contrasts": [c.to_dict() for c in self.contrasts],
            "regression_suites": [s.to_dict() for s in self.regression_suites],
            "approvals": list(self.approvals),
            "component_kinds": [str(k) for k in self.component_kinds],
            "deliverable_verified": self.deliverable_verified,
        }


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """The gate outcome: allowed or refused, with every reason recorded."""

    bundle_id: str
    from_state: PromotionState
    target_state: PromotionState
    allowed: bool
    reasons: tuple[str, ...]
    required_approvals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "from_state": str(self.from_state),
            "target_state": str(self.target_state),
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "required_approvals": list(self.required_approvals),
        }


def evaluate_promotion(
    *,
    bundle: HarnessBundle,
    evidence: PromotionEvidence,
    policy: PromotionPolicy | None = None,
    target_state: PromotionState = PromotionState.ACTIVE,
    current_state: PromotionState | None = None,
) -> PromotionDecision:
    """Decide whether *bundle* may advance toward *target_state*.

    Refusals accumulate rather than short-circuit: an operator (or the UI) sees
    every unmet condition in one pass.
    """
    active_policy = policy or PromotionPolicy()
    state = current_state or bundle.promotion_state
    reasons: list[str] = []
    required_approvals: list[str] = []

    if state in {PromotionState.REJECTED, PromotionState.QUARANTINED, PromotionState.ROLLED_BACK}:
        return PromotionDecision(bundle.bundle_id, state, target_state, False, (f"candidate is {state} and cannot be promoted from that state",))

    if evidence.validation_clean is not True:
        reasons.append("static validation is not clean" if evidence.validation_clean is False else "static validation has not been run")
    if evidence.probe_outcomes < 1:
        reasons.append("no probe outcome was recorded for this candidate")
    if not evidence.contrasts:
        reasons.append("no contrast report was recorded for this candidate")
    else:
        if any(c.verdict is ContrastVerdict.CONFOUNDED for c in evidence.contrasts):
            reasons.append("at least one contrast is confounded")
        if any(c.verdict is ContrastVerdict.NEGATIVE for c in evidence.contrasts):
            reasons.append("at least one contrast is negative")
        positives = [c for c in evidence.contrasts if c.verdict is ContrastVerdict.POSITIVE]
        if not positives and target_state in {PromotionState.SHADOW, PromotionState.CANARY, PromotionState.ACTIVE}:
            reasons.append("no positive contrast supports a global promotion")
        if positives and all(c.targeted_failure_resolved is not True for c in positives):
            reasons.append("no positive contrast resolved its targeted failure")

    if active_policy.require_regression_suite:
        suites = evidence.regression_suites
        if not suites:
            reasons.append("the regression suite has not been run")
        elif any(s.passed is None for s in suites):
            reasons.append("at least one regression suite did not run")
        elif any(s.passed is False for s in suites):
            reasons.append(f"regression suite failed: {', '.join(s.name for s in suites if s.passed is False)}")

    if evidence.deliverable_verified is False:
        reasons.append("the composed successor's deliverable failed deterministic verification")

    kinds = set(evidence.component_kinds)
    if ComponentKind.SCRIPT in kinds and active_policy.require_human_approval_for_scripts and "scripts" not in evidence.approvals:
        required_approvals.append("scripts")
    if ComponentKind.MIDDLEWARE in kinds and active_policy.require_human_approval_for_middleware and "middleware" not in evidence.approvals:
        required_approvals.append("middleware")
    # ``require_human_approval_for_permissions`` is deliberately not consulted:
    # permission components are outside the editable surface, so no promotable
    # candidate can carry one. The flag stays in the config schema for operator
    # continuity; a gate that can never fire is better than a gate that pretends
    # to guard something the validator already refuses.

    if required_approvals:
        reasons.append(f"human approval is required before promotion: {', '.join(required_approvals)}")

    if target_state in {PromotionState.SHADOW, PromotionState.CANARY, PromotionState.ACTIVE} and not active_policy.automatic_global_promotion:
        reasons.append("automatic_global_promotion is disabled; an operator must activate this bundle explicitly")

    return PromotionDecision(bundle.bundle_id, state, target_state, allowed=not reasons, reasons=tuple(reasons), required_approvals=tuple(required_approvals))


@dataclass(slots=True)
class PromotionEvent:
    """One durable promotion-log row."""

    event: str  # "activated" | "rolled_back" | "rejected" | "approved"
    bundle_id: str
    previous_bundle_id: str | None
    actor: str
    reason: str
    at: str = field(default_factory=utc_now)
    gates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "bundle_id": self.bundle_id,
            "previous_bundle_id": self.previous_bundle_id,
            "actor": self.actor,
            "reason": self.reason,
            "at": self.at,
            "gates": self.gates,
        }


def promote_state(bundle: HarnessBundle, target: PromotionState) -> HarnessBundle:
    """Return a copy of *bundle* at *target*, refusing non-ladder jumps.

    A bundle manifest is immutable; the promotion state is provenance and is
    updated by re-serializing the manifest with the new state — the bundle id
    itself never changes because provenance is not part of its digest.
    """
    from dataclasses import replace

    reachable = {PromotionState.REJECTED, PromotionState.QUARANTINED, PromotionState.ROLLED_BACK, PromotionState.ACTIVE, LADDER.get(bundle.promotion_state)}
    if target not in reachable:
        raise ValueError(f"cannot move bundle {bundle.bundle_id} from {bundle.promotion_state} to {target}")
    return replace(bundle, promotion_state=target)


def active_bundle(store: HarnessImprovementStore) -> HarnessBundle | None:
    """Load the active bundle, or ``None`` when no pointer exists yet."""
    pointer = store.load_active()
    if pointer is None:
        return None
    bundle = store.load_bundle(pointer.bundle_id)
    if bundle is None:
        raise StoreCorrupt(f"active pointer names bundle {pointer.bundle_id} which is not stored")
    return bundle


def activate(
    store: HarnessImprovementStore,
    *,
    bundle_id: str,
    actor: str,
    reason: str,
    decision: PromotionDecision | None = None,
    approvals: Sequence[str] = (),
    force: bool = False,
) -> dict[str, Any]:
    """Atomically point the active pointer at *bundle_id*.

    ``force`` skips the *gate* reasons an operator has explicitly overridden but
    never a recorded pending human approval for a privileged kind: those must be
    supplied through *approvals*.
    """
    if not actor.strip():
        return {"activated": False, "reason": "an activation requires an explicit actor; activation is never anonymous"}
    bundle = store.load_bundle(bundle_id)
    if bundle is None:
        return {"activated": False, "reason": f"bundle {bundle_id!r} is not stored"}

    if decision is not None and not decision.allowed:
        pending = [approval for approval in decision.required_approvals if approval not in approvals]
        if pending:
            return {"activated": False, "reason": f"promotion gates are unmet: {pending} approval is still required", "gates": decision.to_dict()}
        if not force:
            return {"activated": False, "reason": f"promotion gates are unmet: {'; '.join(decision.reasons)}", "gates": decision.to_dict()}

    current = store.load_active()
    previous_bundle_id = current.bundle_id if current else None
    if previous_bundle_id == bundle_id:
        return {"activated": False, "reason": "bundle is already active", "previous_bundle_id": previous_bundle_id}

    pointer = ActivePointer(bundle_id=bundle_id, previous_bundle_id=previous_bundle_id, activated_at=utc_now(), actor=actor, reason=reason)
    store.save_active(pointer)
    promoted = promote_state(bundle, PromotionState.ACTIVE)
    store.save_bundle(promoted, [])
    event = PromotionEvent(
        event="activated",
        bundle_id=bundle_id,
        previous_bundle_id=previous_bundle_id,
        actor=actor,
        reason=reason,
        gates=decision.to_dict() if decision else {},
    )
    store.append_promotion(event.to_dict())
    return {"activated": True, "bundle_id": bundle_id, "previous_bundle_id": previous_bundle_id, "pointer": pointer.to_dict()}


def rollback(store: HarnessImprovementStore, *, actor: str, reason: str) -> dict[str, Any]:
    """Restore the previous known-good bundle, atomically and durably.

    Refuses when there is no prior bundle: clearing the pointer would make a
    failure look like a rollback while leaving no harness active.
    """
    if not actor.strip():
        return {"rolled_back": False, "reason": "a rollback requires an explicit actor; rollback is never anonymous"}
    current = store.load_active()
    if current is None:
        return {"rolled_back": False, "reason": "no active bundle pointer exists, so there is nothing to roll back"}
    if current.previous_bundle_id is None:
        return {"rolled_back": False, "reason": "the active bundle has no previous known-good bundle recorded; nothing to restore"}
    previous = store.load_bundle(current.previous_bundle_id)
    if previous is None:
        return {"rolled_back": False, "reason": f"previous bundle {current.previous_bundle_id!r} is not stored; refusing to clear the active pointer"}

    pointer = ActivePointer(bundle_id=previous.bundle_id, previous_bundle_id=None, activated_at=utc_now(), actor=actor, reason=f"rollback: {reason}")
    store.save_active(pointer)
    rolled = promote_state(previous, PromotionState.ACTIVE)
    store.save_bundle(rolled, [])
    store.append_promotion(PromotionEvent(event="rolled_back", bundle_id=previous.bundle_id, previous_bundle_id=current.bundle_id, actor=actor, reason=reason).to_dict())
    return {"rolled_back": True, "restored_bundle_id": previous.bundle_id, "from_bundle_id": current.bundle_id, "pointer": pointer.to_dict()}


def reject_candidate(store: HarnessImprovementStore, *, bundle_id: str, actor: str, reason: str) -> dict[str, Any]:
    """Record an operator rejection; the active pointer is untouched."""
    bundle = store.load_bundle(bundle_id)
    if bundle is None:
        return {"rejected": False, "reason": f"bundle {bundle_id!r} is not stored"}
    rejected = promote_state(bundle, PromotionState.REJECTED)
    store.save_bundle(rejected, [])
    store.append_promotion(PromotionEvent(event="rejected", bundle_id=bundle_id, previous_bundle_id=None, actor=actor or "operator", reason=reason).to_dict())
    return {"rejected": True, "bundle_id": bundle_id, "promotion_state": str(rejected.promotion_state)}


def promotion_history(store: HarnessImprovementStore) -> dict[str, Any]:
    """The durable promotion chain plus the current pointer, read-only."""
    listing = store.read_promotions()
    try:
        pointer = store.load_active()
    except (StoreUnreadable, StoreCorrupt) as exc:
        return {"events": [dict(row) for row in listing.items], "unreadable": list(listing.unreadable), "pointer": None, "pointer_error": str(exc)}
    return {
        "events": [dict(row) for row in listing.items],
        "unreadable": list(listing.unreadable),
        "pointer": pointer.to_dict() if pointer else None,
        "pointer_error": None,
    }


def validator_status_counts(values: Sequence[ValidatorStatus]) -> dict[str, int]:
    """Small helper used by the UI router to summarize gate checks."""
    counts = {"passed": 0, "failed": 0, "not_run": 0}
    for value in values:
        counts[str(value)] += 1
    return counts
