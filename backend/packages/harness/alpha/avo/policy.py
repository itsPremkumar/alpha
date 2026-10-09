"""The policy gate: the one place an action is allowed or refused.

Everything else in this package proposes. This decides. The decision is
server-owned end to end:

* **Risk is computed, never declared.** The action carries
  ``self_assessed_risk`` for the record; :func:`compute_risk` derives the class
  the gate actually applies from the action type, its scopes and the profile it
  runs under. A model that labels its own write ``low`` changes one recorded
  field and nothing else.
* **Default-deny is structural.** The capability profile's
  ``allowed_action_types`` is an allow-list; a type that is not in it is
  refused with the profile named. The path lists are checked on their
  *normalised* form through :mod:`alpha.avo.paths`, so ``../`` and an absolute
  path are refusals rather than inputs.
* **Approval is required, not requested.** When the approval policy says a
  risk class needs a human, the action is refused unless a granted approval id
  is presented. ``notify`` records the intent and proceeds; ``require`` does
  not proceed. The gate never *asks* for approval inside ``decide`` — a run
  waiting on a human is a state the lifecycle owns.
* **Budget is reserved before execution.** The reservation happens inside the
  gate so "allowed" and "funds held" are one atomic outcome: an action allowed
  without its reservation would be an action that spent budget nobody tracked.
  An action refused on scope never reserves, so a refusal cannot hold budget
  hostage either.

The refusal vocabulary is fixed (:data:`REASON_CODES`) so a log line and
Alpha's error taxonomy can agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from .budgets import BudgetExceeded, BudgetKind, BudgetLedger, reservations_for_action
from .contracts import ActionType, PolicyDecision, ProposedAction, RiskClass
from .paths import PathRefused, matches_any
from .profiles import ApprovalPolicy, CapabilityProfile

__all__ = [
    "POLICY_VERSION",
    "REASON_CODES",
    "PolicyGate",
    "ReasonCode",
    "compute_risk",
]

#: Bumped when a decision's semantics change. Recorded on every decision so a
#: decision made under yesterday's rules is identifiable as such.
POLICY_VERSION = "avo-policy-1"


class ReasonCode(StrEnum):
    """The closed set of refusal/consent reasons. Never free text at the boundary."""

    OK = "ok"
    PROFILE_DISABLED = "profile_disabled"
    ACTION_NOT_PERMITTED = "action_type_not_permitted"
    PROTECTED_PATH = "protected_path"
    WRITE_OUTSIDE_SCOPE = "write_outside_scope"
    READ_OUTSIDE_SCOPE = "read_outside_scope"
    PATH_REFUSED = "path_refused"
    FORBIDDEN_ACTION = "forbidden_action"
    APPROVAL_REQUIRED = "approval_required"
    BUDGET_EXHAUSTED = "budget_exhausted"


#: Everything a decision may carry. A reason outside this set means the gate
#: grew a branch nobody reviewed.
REASON_CODES: frozenset[str] = frozenset(ReasonCode)


#: Base class -> risk, from the *operation*, not the requester. Promotion and
#: full-suite runs sit above file writes because they change what ships and
#: how long the run blocks; reads sit at the floor.
_ACTION_RISK: dict[ActionType, RiskClass] = {
    ActionType.READ_FILE: RiskClass.LOW,
    ActionType.LIST_DIRECTORY: RiskClass.LOW,
    ActionType.SEARCH_REPOSITORY: RiskClass.LOW,
    ActionType.GIT_STATUS: RiskClass.LOW,
    ActionType.GIT_DIFF: RiskClass.LOW,
    ActionType.CREATE_CANDIDATE_WORKTREE: RiskClass.MEDIUM,
    ActionType.WRITE_CANDIDATE_FILE: RiskClass.MEDIUM,
    ActionType.RUN_TARGETED_CHECK: RiskClass.MEDIUM,
    ActionType.RUN_REGRESSION_SUITE: RiskClass.MEDIUM,
    ActionType.RUN_FULL_SUITE: RiskClass.HIGH,
    ActionType.REQUEST_PROMOTION: RiskClass.HIGH,
}

_RISK_ORDER: tuple[RiskClass, ...] = (RiskClass.LOW, RiskClass.MEDIUM, RiskClass.HIGH, RiskClass.DESTRUCTIVE)


def _escalate(base: RiskClass, steps: int) -> RiskClass:
    index = min(_RISK_ORDER.index(base) + steps, len(_RISK_ORDER) - 1)
    return _RISK_ORDER[index]


def compute_risk(action: ProposedAction, profile: CapabilityProfile) -> RiskClass:
    """The risk class the gate applies. Deterministic, server-side.

    Two escalations, both derived from facts the model does not control:

    * an action that is **not reversible** escalates one step — the plan's
      "whether rollback is tested" becomes "can this be undone at all";
    * an action running under a profile with **network access** escalates one
      step — reach beyond the workspace widens blast radius whatever the
      action type says.

    ``self_assessed_risk`` is deliberately not an input.
    """
    risk = _ACTION_RISK.get(action.action_type, RiskClass.HIGH)
    if not action.reversible:
        risk = _escalate(risk, 1)
    if profile.network != "denied":
        risk = _escalate(risk, 1)
    return risk


@dataclass
class PolicyGate:
    """Decide one run's actions against its server-resolved profiles.

    The gate is constructed with the *resolved* profiles — never with their
    identifiers — because resolution is the moment an unknown id becomes an
    exception (:class:`~alpha.avo.profiles.UnknownProfile`) instead of a
    silently-empty allow-list.
    """

    capability: CapabilityProfile
    approval: ApprovalPolicy
    ledger: BudgetLedger
    #: Approval ids already granted by an operator for this run. The gate only
    #: checks membership; minting an approval is an operator action elsewhere.
    granted_approvals: set[str] = field(default_factory=set)
    decisions: list[PolicyDecision] = field(default_factory=list)

    # ------------------------------------------------------------------
    # refusal checks, cheapest and most decisive first
    # ------------------------------------------------------------------
    def _profile_refusal(self, action: ProposedAction) -> ReasonCode | None:
        if not self.capability.enabled:
            return ReasonCode.PROFILE_DISABLED
        if not self.capability.permits_action(action.action_type):
            return ReasonCode.ACTION_NOT_PERMITTED
        return None

    def _path_refusal(self, action: ProposedAction) -> ReasonCode | None:
        """Validate every scoped path against the profile's three lists.

        The order is deliberate: protected is checked *first* on every list, so
        a write that is both inside ``write_paths`` and inside a protected
        prefix resolves to ``protected_path`` rather than sliding through on
        the earlier match.
        """
        checks: list[tuple[list[str], ReasonCode]] = [
            (action.write_scope, ReasonCode.WRITE_OUTSIDE_SCOPE),
            (action.read_scope, ReasonCode.READ_OUTSIDE_SCOPE),
        ]
        for paths, outside_code in checks:
            for path in paths:
                try:
                    matched = matches_any(path, self.capability.protected_paths)
                except PathRefused:
                    return ReasonCode.PATH_REFUSED
                if matched is not None:
                    return ReasonCode.PROTECTED_PATH
                try:
                    allowed = self.capability.write_paths if outside_code is ReasonCode.WRITE_OUTSIDE_SCOPE else self.capability.read_paths
                    matched = matches_any(path, allowed)
                except PathRefused:
                    return ReasonCode.PATH_REFUSED
                if matched is None:
                    return outside_code
        return None

    def _forbidden_refusal(self, action: ProposedAction) -> ReasonCode | None:
        """Map the action onto the approval policy's forbidden list.

        The forbidden list names *effects* (``rotate_secret``,
        ``modify_policy_code``), not action types, so the match is on the
        effect the action declares in ``arguments.effect`` plus the action type
        itself. Anything that matches is refused outright — no tier table can
        authorise it.
        """
        declared = str(action.arguments.get("effect", "") or "")
        candidates = {declared, action.action_type.value}
        for token in self.approval.forbidden_actions:
            if token in candidates:
                return ReasonCode.FORBIDDEN_ACTION
        return None

    # ------------------------------------------------------------------
    def decide(
        self,
        action: ProposedAction,
        *,
        granted_approvals: Iterable[str] | None = None,
    ) -> PolicyDecision:
        """Decide one action. Every refusal path returns a decision, never ``None``.

        Order matters: scope checks run *before* budget, so an action refused
        on scope never consumes a reservation slot; budget is checked before
        approval, so a run out of budget reports exhaustion rather than
        parking on a human for work it cannot do.
        """
        if granted_approvals:
            self.granted_approvals.update(granted_approvals)

        risk = compute_risk(action, self.capability)
        reservation: dict[str, float] = {}
        approval_required = False
        approval_id: str | None = None
        budget_reason = ""

        refusal = self._profile_refusal(action)
        if refusal is None:
            refusal = self._path_refusal(action)
        if refusal is None:
            refusal = self._forbidden_refusal(action)
        if refusal is None:
            requirement = self.approval.requirement_for(risk.value)
            evidence = action.arguments.get("approval_id")
            evidence = str(evidence) if evidence else None
            if requirement == "require" and evidence not in self.granted_approvals:
                refusal = ReasonCode.APPROVAL_REQUIRED
                approval_required = True
            elif requirement == "require":
                approval_id = evidence

        if refusal is None:
            # Reserve only for an action that is otherwise allowed.
            try:
                for kind, amount in reservations_for_action(BudgetKind.ACTION).items():
                    self.ledger.reserve(kind, amount, experiment_id=action.experiment_id)
                    reservation[kind.value] = amount
            except BudgetExceeded as exc:
                refusal = ReasonCode.BUDGET_EXHAUSTED
                # Whatever was reserved before the failing kind is returned,
                # so a budget refusal does not leak a partial reservation.
                for reserved_kind, amount in reservation.items():
                    self.ledger.release(BudgetKind(reserved_kind), amount, experiment_id=action.experiment_id)
                reservation = {}
                budget_reason = exc.reason
            else:
                budget_reason = ""

        allowed = refusal is None
        reason_code = refusal or ReasonCode.OK
        if reason_code not in REASON_CODES:  # pragma: no cover - guarded vocabulary
            raise AssertionError(f"policy gate produced an unregistered reason code {reason_code!r}")

        decision = PolicyDecision(
            action_id=action.action_id,
            action_digest=action.digest,
            experiment_id=action.experiment_id,
            allowed=allowed,
            reason_code=str(reason_code.value),
            reason=budget_reason if refusal is ReasonCode.BUDGET_EXHAUSTED else _explain(reason_code, action, self),
            policy_version=POLICY_VERSION,
            required_capability=action.action_type.value if allowed else None,
            risk_class=risk,
            approval_required=approval_required,
            approval_id=approval_id,
            budget_reservation=reservation,
        )
        self.decisions.append(decision)
        return decision

    # ------------------------------------------------------------------
    @property
    def allowed_count(self) -> int:
        return sum(1 for d in self.decisions if d.allowed)

    @property
    def denied_count(self) -> int:
        return len(self.decisions) - self.allowed_count

    def snapshot(self) -> dict[str, object]:
        return {
            "policy_version": POLICY_VERSION,
            "capability_profile": self.capability.profile_id,
            "approval_policy": self.approval.policy_id,
            "decisions": len(self.decisions),
            "allowed": self.allowed_count,
            "denied": self.denied_count,
            "budget": self.ledger.snapshot(),
        }


def _explain(reason: ReasonCode, action: ProposedAction, gate: PolicyGate) -> str:
    """Human-readable text for a decision. Derived, never caller-supplied."""
    if reason is ReasonCode.OK:
        return f"allowed: {action.action_type.value} under profile {gate.capability.profile_id} at risk {compute_risk(action, gate.capability).value}"
    if reason is ReasonCode.PROFILE_DISABLED:
        return f"capability profile {gate.capability.profile_id} exists but is disabled; it cannot be reached by naming it"
    if reason is ReasonCode.ACTION_NOT_PERMITTED:
        return f"{action.action_type.value} is not in the allow-list of profile {gate.capability.profile_id}"
    if reason is ReasonCode.PROTECTED_PATH:
        return "a scoped path matches a protected prefix; the evaluator, policy, tests and config are never candidate-writable"
    if reason is ReasonCode.WRITE_OUTSIDE_SCOPE:
        return f"write scope {action.write_scope} is outside the profile's writable paths {gate.capability.write_paths}"
    if reason is ReasonCode.READ_OUTSIDE_SCOPE:
        return f"read scope {action.read_scope} is outside the profile's readable paths {gate.capability.read_paths}"
    if reason is ReasonCode.PATH_REFUSED:
        return "a scoped path is not a plain relative path (traversal, absolute, URL or NUL)"
    if reason is ReasonCode.FORBIDDEN_ACTION:
        return f"the action declares an effect the approval policy {gate.approval.policy_id} forbids at every tier"
    if reason is ReasonCode.APPROVAL_REQUIRED:
        return f"risk requires operator approval under {gate.approval.policy_id} and no granted approval id was presented"
    if reason is ReasonCode.BUDGET_EXHAUSTED:
        return "a budget ceiling was reached while reserving for this action"
    return reason.value  # pragma: no cover - guarded vocabulary
