"""Value-of-information scheduling: what to do NEXT, not which model is strongest (Feature I / plan §10).

The mesh routes *who* does a step; this module routes *whether to keep
working at all*. Given candidate next actions it applies the plan's formula
exactly::

    ExpectedNetValue(action) = ExpectedRiskOrErrorReduction
                             + ExpectedRequirementCoverageGain
                             + ExpectedDecisionClarity
                             - Cost - Latency - NewFailureRisk

and picks the highest-value action that is still available and still worth
asking, then decides whether to stop and return.

Five rules decide the shape of this module:

* **The number is an estimate, never knowledge of the future.** Plan §10 says
  so explicitly ("This is an estimate, not a claim that Alpha can know the
  future"), so every expectation on a :class:`CandidateAction` must be stated
  by the caller — a default ``0.0`` would be a fabricated prediction that
  reads as a measurement — and :attr:`VoIEstimate.speculative` is ``True``
  whenever any term came from :data:`UNPRICED_PRIOR` instead of a number the
  caller supplied.
* **An unpriced term is UNPRICED, never free.** ``cost_usd=None`` /
  ``latency_seconds=None`` mean "nobody priced this", so
  :func:`expected_net_value` substitutes the documented :data:`UNPRICED_PRIOR`
  penalty and *names the substitution in the component key itself*
  (``cost_unpriced_prior`` instead of ``cost``). A ``0.0`` stand-in would make
  an unpriced action look like the cheapest possible one.
* **A settled point is settled.** Plan §10 forbids "repeatedly asking agents to
  rephrase an already settled point", so :func:`choose_next_action` drops any
  candidate whose description matches a settled point even when its estimated
  value is the highest on the table. The match is a deliberately crude
  whitespace/case-folded containment test — keyword matching, not
  understanding, exactly like ``fingerprint.py``'s baseline.
* **Stopping is a conjunction, and it is auditable.** :func:`should_stop`
  stops only when *every* acceptance criterion is met **and** the unresolved
  risk is at or below the threshold, and its ``reason`` always names which
  criterion or which risk blocked it. A polished shallow answer with real
  unresolved risk keeps the loop running (plan §10: "continue deeper on
  high-impact uncertainty even if a shallow answer looks polished").
* **Nothing here executes anything.** These are pure functions over declared
  expectations. No test calls a model, a tool or a test runner, and no
  production path reads this module yet — it is a tested planner, not a
  scheduler anything drives.

The weights are not weighted at all: the plan's formula is a plain sum of six
terms, so its only tunable constant is :data:`UNPRICED_PRIOR` (and the stop
threshold a caller passes). Both are documented starting values, not
measurements of worth.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: An action description is a short justification, not a prompt. Refused
#: above this bound, never truncated: a silently cut description would stop
#: naming the action it justifies.
MAX_DESCRIPTION_CHARS = 400

#: The penalty substituted for an action nobody priced. Conservative on
#: purpose — an unpriced action is assumed to cost *something*, so it cannot
#: win on a fabricated zero. A documented starting constant, not a measured
#: price; every substitution names itself in ``VoIEstimate.components``.
UNPRICED_PRIOR = 0.05

#: Default unresolved-risk ceiling for :func:`should_stop`. At or below this
#: value, met acceptance criteria mean the work is done; above it, plan §10
#: says to keep going.
DEFAULT_UNRESOLVED_RISK_THRESHOLD = 0.2

#: How many unmet criteria a stop reason quotes before it truncates. The
#: decision still has to be auditable, so the count of the rest is named.
MAX_QUOTED_UNMET_CRITERIA = 8

#: The four always-measured terms of the plan's formula.
_REQUIRED_COMPONENTS = (
    "expected_risk_or_error_reduction",
    "expected_coverage_gain",
    "expected_decision_clarity",
    "new_failure_risk",
)

#: A cost term is either measured (``cost``) or a substituted prior
#: (``cost_unpriced_prior``). The key names which, so no reader can mistake a
#: prior for a price.
_COST_COMPONENTS = ("cost", "cost_unpriced_prior")

#: The same split for latency.
_LATENCY_COMPONENTS = ("latency", "latency_unpriced_prior")

_PRIOR_COMPONENTS = ("cost_unpriced_prior", "latency_unpriced_prior")


class ActionKind(StrEnum):
    """The candidate next actions plan §10 lists, in the plan's order."""

    RUN_TEST = "run_test"
    READ_PRIMARY_SOURCE = "read_primary_source"
    ASK_SPECIALIST = "ask_specialist"
    INSPECT_FILE = "inspect_file"
    COMPARE_ALTERNATIVES = "compare_alternatives"
    SWITCH_MODEL_FAMILY = "switch_model_family"
    CREATE_COUNTEREXAMPLE = "create_counterexample"
    REQUEST_MISSING_CONSTRAINT = "request_missing_constraint"
    STOP_AND_RETURN = "stop_and_return"


def _fraction(value: float, field: str) -> float:
    """Validate one [0, 1] fraction. Bounds are refused, never clamped.

    ``bool`` is refused explicitly: ``True`` is an ``int`` in Python, so a
    flag passed by mistake would otherwise become the number 1.0 — an
    expectation of total certainty that nobody declared.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{field} must be a finite number in [0, 1], got {value!r}")
    return float(value)


def _non_negative(value: float, field: str) -> float:
    """Validate one finite, non-negative quantity (a price or a duration)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0.0:
        raise ValueError(f"{field} must be a finite number >= 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class CandidateAction:
    """One possible next action and the caller's expectations about it.

    The three ``expected_*`` fractions and ``new_failure_risk`` are REQUIRED:
    they are the caller's declared estimate, and a default would be a
    prediction nobody made. ``cost_usd`` and ``latency_seconds`` are optional
    because they are frequently genuinely unknown — ``None`` means UNPRICED
    (substituted with :data:`UNPRICED_PRIOR` and named in the result), never
    ``0.0``.

    ``available=False`` REQUIRES a non-empty ``blocking_reason`` (an
    unavailable action nobody can explain is an assertion), and an
    ``available=True`` action may not carry one at all: a "blocked" reason on a
    runnable action is a contradiction a later reader would have to untangle.
    """

    kind: ActionKind
    description: str
    expected_risk_or_error_reduction: float
    expected_coverage_gain: float
    expected_decision_clarity: float
    new_failure_risk: float
    cost_usd: float | None = None
    latency_seconds: float | None = None
    available: bool = True
    blocking_reason: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", ActionKind(str(self.kind)))
        except ValueError as error:
            allowed = ", ".join(kind.value for kind in ActionKind)
            raise ValueError(f"unknown kind {self.kind!r}; expected one of: {allowed}") from error
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be a non-empty string: an action nobody can name is not auditable")
        if len(self.description) > MAX_DESCRIPTION_CHARS:
            raise ValueError(f"description must be <= {MAX_DESCRIPTION_CHARS} characters, got {len(self.description)}")
        object.__setattr__(self, "expected_risk_or_error_reduction", _fraction(self.expected_risk_or_error_reduction, "expected_risk_or_error_reduction"))
        object.__setattr__(self, "expected_coverage_gain", _fraction(self.expected_coverage_gain, "expected_coverage_gain"))
        object.__setattr__(self, "expected_decision_clarity", _fraction(self.expected_decision_clarity, "expected_decision_clarity"))
        object.__setattr__(self, "new_failure_risk", _fraction(self.new_failure_risk, "new_failure_risk"))
        if self.cost_usd is not None:
            object.__setattr__(self, "cost_usd", _non_negative(self.cost_usd, "cost_usd"))
        if self.latency_seconds is not None:
            object.__setattr__(self, "latency_seconds", _non_negative(self.latency_seconds, "latency_seconds"))
        if not isinstance(self.available, bool):
            raise ValueError(f"available must be a bool, got {self.available!r}")
        if not self.available and not (isinstance(self.blocking_reason, str) and self.blocking_reason.strip()):
            raise ValueError(f"blocking_reason must be a non-empty string when available is False: an unavailable action nobody can explain is an assertion (action: {self.description!r})")
        if self.available and self.blocking_reason is not None:
            raise ValueError(f"blocking_reason must be None when available is True: {self.blocking_reason!r} contradicts an action that is available (action: {self.description!r})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "description": self.description,
            "expected_risk_or_error_reduction": self.expected_risk_or_error_reduction,
            "expected_coverage_gain": self.expected_coverage_gain,
            "expected_decision_clarity": self.expected_decision_clarity,
            "new_failure_risk": self.new_failure_risk,
            "cost_usd": self.cost_usd,
            "latency_seconds": self.latency_seconds,
            "available": self.available,
            "blocking_reason": self.blocking_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateAction:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            # Coerced (and validated with the friendly message) in __post_init__.
            kind=data["kind"],
            description=str(data["description"]),
            expected_risk_or_error_reduction=float(data["expected_risk_or_error_reduction"]),
            expected_coverage_gain=float(data["expected_coverage_gain"]),
            expected_decision_clarity=float(data["expected_decision_clarity"]),
            new_failure_risk=float(data["new_failure_risk"]),
            cost_usd=None if data.get("cost_usd") is None else float(data["cost_usd"]),
            latency_seconds=None if data.get("latency_seconds") is None else float(data["latency_seconds"]),
            available=bool(data.get("available", True)),
            blocking_reason=data.get("blocking_reason"),
        )


@dataclass(frozen=True)
class VoIEstimate:
    """One action's expected net value, with the terms it was made of.

    ``components`` holds the SIGNED terms of plan §10's formula, so they sum
    (with ``math.fsum``) to ``expected_net_value`` exactly — and construction
    refuses a pair that does not add up. A benefit is positive, a penalty
    negative; a substituted prior lives under a key that says so
    (``cost_unpriced_prior``), which is also what sets ``speculative``.

    ``speculative`` is ``True`` when any term came from :data:`UNPRICED_PRIOR`
    rather than a number the caller supplied, and it is refused when it
    disagrees with the components. It means "this number contains a default",
    **not** knowledge of what will happen: the whole estimate is a prediction
    about a future action, which no function here can observe.
    """

    action: CandidateAction
    expected_net_value: float
    components: dict[str, float]
    speculative: bool

    def __post_init__(self) -> None:
        if not isinstance(self.action, CandidateAction):
            raise ValueError(f"action must be a CandidateAction, got {self.action!r}")
        if isinstance(self.expected_net_value, bool) or not isinstance(self.expected_net_value, (int, float)) or not math.isfinite(float(self.expected_net_value)):
            raise ValueError(f"expected_net_value must be a finite number, got {self.expected_net_value!r}")
        object.__setattr__(self, "expected_net_value", float(self.expected_net_value))
        components = {str(name): float(value) for name, value in dict(self.components).items()}
        for name in _REQUIRED_COMPONENTS:
            if name not in components:
                raise ValueError(f"components must contain {name!r}: the plan's formula has six terms and none of them is optional")
        for family in (_COST_COMPONENTS, _LATENCY_COMPONENTS):
            if not any(name in components for name in family):
                expected = " or ".join(repr(name) for name in family)
                raise ValueError(f"components must contain {expected}: an unpriced term is a substituted prior, never an omitted one")
        allowed = set(_REQUIRED_COMPONENTS) | set(_COST_COMPONENTS) | set(_LATENCY_COMPONENTS)
        for name in components:
            if name not in allowed:
                raise ValueError(f"unknown component {name!r}; the formula's terms are: {', '.join(sorted(allowed))}")
            if not math.isfinite(components[name]):
                raise ValueError(f"component {name!r} must be a finite number, got {components[name]!r}")
        object.__setattr__(self, "components", components)
        if not math.isclose(math.fsum(components.values()), self.expected_net_value, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"expected_net_value {self.expected_net_value!r} does not equal the sum of its components {math.fsum(components.values())!r}: an estimate that does not add up cannot be audited")
        substituted = any(name in components for name in _PRIOR_COMPONENTS)
        if not isinstance(self.speculative, bool):
            raise ValueError(f"speculative must be a bool, got {self.speculative!r}")
        if substituted and not self.speculative:
            raise ValueError("speculative must be True when a component came from the UNPRICED_PRIOR: a substituted default is not a measurement")
        if self.speculative and not substituted:
            raise ValueError("speculative must be False when every component was supplied by the caller: flagging a fully stated estimate speculative would hide that it was declared, not defaulted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "expected_net_value": self.expected_net_value,
            "components": dict(self.components),
            "speculative": self.speculative,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoIEstimate:
        return cls(
            action=CandidateAction.from_dict(data["action"]),
            expected_net_value=float(data["expected_net_value"]),
            components={str(name): float(value) for name, value in data["components"].items()},
            speculative=bool(data["speculative"]),
        )


@dataclass(frozen=True)
class StopDecision:
    """Whether to stop, and the audited reason for it.

    ``reason`` is REQUIRED non-empty: plan §10 insists the stop decision be
    recorded "so the decision can be audited", and a bare boolean is not a
    record. ``unmet_criteria`` and ``unresolved_risk`` travel beside it so the
    reason can be re-derived rather than believed.
    """

    should_stop: bool
    reason: str
    unmet_criteria: tuple[str, ...] = ()
    unresolved_risk: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.should_stop, bool):
            raise ValueError(f"should_stop must be a bool, got {self.should_stop!r}")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string: the stop decision has to be auditable, and a reason nobody wrote is not")
        unmet = tuple(self.unmet_criteria)
        for criterion in unmet:
            if not isinstance(criterion, str) or not criterion.strip():
                raise ValueError("unmet_criteria entries must be non-empty strings")
        object.__setattr__(self, "unmet_criteria", unmet)
        object.__setattr__(self, "unresolved_risk", _fraction(self.unresolved_risk, "unresolved_risk"))
        if self.should_stop and unmet:
            raise ValueError(f"should_stop is True while {len(unmet)} acceptance criteria are unmet: one decision, one honest state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_stop": self.should_stop,
            "reason": self.reason,
            "unmet_criteria": list(self.unmet_criteria),
            "unresolved_risk": self.unresolved_risk,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StopDecision:
        return cls(
            should_stop=bool(data["should_stop"]),
            reason=str(data["reason"]),
            unmet_criteria=tuple(str(criterion) for criterion in data.get("unmet_criteria", ())),
            unresolved_risk=float(data.get("unresolved_risk", 0.0)),
        )


def expected_net_value(action: CandidateAction) -> VoIEstimate:
    """Apply plan §10's formula to one action and return the auditable sum.

    ``risk reduction + coverage gain + decision clarity - cost - latency -
    new failure risk``, with :data:`UNPRICED_PRIOR` substituted for an
    unpriced cost or latency **and named in the component key**, so the
    estimate says which of its terms were declared and which were defaulted.

    An ``available=False`` action is refused by name rather than scored:
    scoring an action that cannot run would let the highest number on the
    table be one nobody can take.
    """
    if not isinstance(action, CandidateAction):
        raise ValueError(f"expected_net_value expects a CandidateAction, got {action!r}")
    if not action.available:
        raise ValueError(f"cannot value an unavailable action {action.description!r} ({action.blocking_reason!r}): an action that cannot run has no expected value")

    components: dict[str, float] = {
        "expected_risk_or_error_reduction": action.expected_risk_or_error_reduction,
        "expected_coverage_gain": action.expected_coverage_gain,
        "expected_decision_clarity": action.expected_decision_clarity,
    }
    if action.cost_usd is None:
        components["cost_unpriced_prior"] = -UNPRICED_PRIOR
    else:
        components["cost"] = -action.cost_usd
    if action.latency_seconds is None:
        components["latency_unpriced_prior"] = -UNPRICED_PRIOR
    else:
        components["latency"] = -action.latency_seconds
    components["new_failure_risk"] = -action.new_failure_risk

    speculative = action.cost_usd is None or action.latency_seconds is None
    return VoIEstimate(action=action, expected_net_value=math.fsum(components.values()), components=components, speculative=speculative)


def _normalized(text: str) -> str:
    """Case-folded, whitespace-collapsed text — the only matching this does."""
    return " ".join(text.split()).casefold()


def _is_settled(description: str, settled_points: tuple[str, ...]) -> bool:
    """Does this description re-ask an already settled point?

    A candidate is suppressed when its description *contains* a settled
    point (or equals it) after normalization. That catches the plan's actual
    failure mode — "please restate, once more, that the rate limit is 100
    req/s" — and nothing more: this is substring matching, not semantic
    similarity, so a genuinely new action phrased differently still survives.
    """
    candidate = _normalized(description)
    for point in settled_points:
        needle = _normalized(point)
        if needle and needle in candidate:
            return True
    return False


def choose_next_action(actions: Sequence[CandidateAction], *, settled_points: Sequence[str] = ()) -> VoIEstimate | None:
    """The highest-value available action that is not a settled point, or ``None``.

    Unavailable actions are skipped rather than scored, and a candidate whose
    description matches a ``settled_point`` is skipped **however highly it
    scores** — plan §10 forbids re-asking agents to rephrase a point that is
    already settled. Ties keep declaration order, so the choice is
    deterministic.

    ``None`` means there is nothing left worth doing here: every candidate was
    unavailable, already settled, or absent. That is the caller's cue to consult
    :func:`should_stop` (or to gather more candidates) — it is never a
    fabricated recommendation to stop.
    """
    settled = tuple(str(point) for point in settled_points)
    best: VoIEstimate | None = None
    for candidate in actions:
        if not isinstance(candidate, CandidateAction):
            raise ValueError(f"actions must contain CandidateAction rows, got {candidate!r}")
        if not candidate.available:
            continue
        if _is_settled(candidate.description, settled):
            continue
        estimate = expected_net_value(candidate)
        if best is None or estimate.expected_net_value > best.expected_net_value:
            best = estimate
    return best


def should_stop(*, unmet_criteria: Sequence[str], unresolved_risk: float, unresolved_risk_threshold: float = DEFAULT_UNRESOLVED_RISK_THRESHOLD) -> StopDecision:
    """Stop only when every criterion is met AND the risk is at the floor.

    Plan §10's first required control: "Stop on satisfied acceptance criteria
    unless meaningful unresolved risk remains." So the two conditions are a
    conjunction — met criteria with real unresolved risk keeps the work going,
    because "continue deeper on high-impact uncertainty even if a shallow
    answer looks polished".

    The ``reason`` always names what blocked stopping: every unmet criterion
    (up to ``MAX_QUOTED_UNMET_CRITERIA``, then a count of the rest) and the
    risk against the threshold. Both are refused outside [0, 1] rather than
    clamped, because a risk of 1.4 or a threshold of -0.1 is a caller bug, not
    a value to round.
    """
    unmet = tuple(str(criterion) for criterion in unmet_criteria)
    for criterion in unmet:
        if not criterion.strip():
            raise ValueError("unmet_criteria entries must be non-empty strings: an unnamed criterion cannot be reported as blocking a stop")
    risk = _fraction(unresolved_risk, "unresolved_risk")
    threshold = _fraction(unresolved_risk_threshold, "unresolved_risk_threshold")

    quoted = [f"{criterion!r}" for criterion in unmet[:MAX_QUOTED_UNMET_CRITERIA]]
    if len(unmet) > MAX_QUOTED_UNMET_CRITERIA:
        quoted.append(f"…+{len(unmet) - MAX_QUOTED_UNMET_CRITERIA} more unmet criteria")
    unmet_clause = f"{len(unmet)} unmet acceptance criteria: {', '.join(quoted)}" if unmet else ""
    risk_clause = f"unresolved risk {risk:.3f} above threshold {threshold:.3f}" if risk > threshold else ""

    if unmet_clause or risk_clause:
        blockers = "; and ".join(part for part in (unmet_clause, risk_clause) if part)
        return StopDecision(should_stop=False, reason=f"not stopping: {blockers}", unmet_criteria=unmet, unresolved_risk=risk)
    return StopDecision(
        should_stop=True,
        reason=f"stopping: every acceptance criterion is met and unresolved risk {risk:.3f} <= threshold {threshold:.3f}",
        unmet_criteria=unmet,
        unresolved_risk=risk,
    )


__all__ = [
    "DEFAULT_UNRESOLVED_RISK_THRESHOLD",
    "MAX_DESCRIPTION_CHARS",
    "MAX_QUOTED_UNMET_CRITERIA",
    "UNPRICED_PRIOR",
    "ActionKind",
    "CandidateAction",
    "StopDecision",
    "VoIEstimate",
    "choose_next_action",
    "expected_net_value",
    "should_stop",
]
