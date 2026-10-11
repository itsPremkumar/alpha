"""GRPO-inspired group-relative orchestration (GRO): value types and run state.

The orchestration run is a durable state machine, not an opaque async call:
every transition is checked against a closed table, recorded with timestamp,
actor and reason, and terminal states are explicit. A lost worker, missing
event, HTTP timeout or process restart is never interpreted as successful
completion.

The load-bearing honesty rule of the whole package lives here: **relative rank
is diagnostic only.** It orders eligible candidates and estimates whether
another attempt is worth funding; it can never make an ineligible candidate
eligible and never overrides a hard gate (see ``alpha.gro.evaluation``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

POLICY_VERSION = "gro-policy-v1"
STRATEGY_REGISTRY_VERSION = "gro-strategies-v1"
EVALUATOR_VERSION = "gro-criteria-relative-v1"

_MAX_EVENT_HISTORY = 256
_BOUNDED_TEXT = 4000


class IllegalGroTransition(RuntimeError):
    """Raised when a run is asked to take a transition outside the closed table."""


class TaskType(StrEnum):
    """Normalized task classification; decides strategy set and score weights."""

    CODING = "coding"
    RESEARCH = "research"
    PLANNING = "planning"
    GENERAL = "general"


class RiskTier(StrEnum):
    """Task tier. ``rank`` (not the member) is the comparison order."""

    TRIVIAL = "trivial"
    ORDINARY = "ordinary"
    UNCERTAIN = "uncertain"
    COMPLEX = "complex"

    @property
    def rank(self) -> int:
        return _TIER_RANKS[self]


_TIER_RANKS: dict[RiskTier, int] = {
    RiskTier.TRIVIAL: 1,
    RiskTier.ORDINARY: 2,
    RiskTier.UNCERTAIN: 3,
    RiskTier.COMPLEX: 4,
}


class RunMode(StrEnum):
    """Policy decision for one task (plan section 9, phase A)."""

    SINGLE = "single"
    CANDIDATE_GROUP = "candidate_group"
    HUMAN_APPROVAL_FIRST = "human_approval_first"


class GroRunState(StrEnum):
    """Run lifecycle states. Terminal states are listed in ``TERMINAL_STATES``."""

    RECEIVED = "received"
    NORMALIZED = "normalized"
    POLICY_DECIDED = "policy_decided"
    GROUP_CREATED = "group_created"
    SINGLE_ATTEMPT = "single_attempt"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    RANKED = "ranked"
    SELECTED = "selected"
    NO_ACCEPTABLE_CANDIDATE = "no_acceptable_candidate"
    REPAIR_GROUP = "repair_group"
    NEEDS_HUMAN = "needs_human"
    PROMOTED = "promoted"
    PARTIAL_RESULT = "partial_result"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"


#: Explicit terminal states. Anything not in this set may still move (under
#: ``_TRANSITIONS``); nothing may move out of these.
TERMINAL_STATES: frozenset[GroRunState] = frozenset(
    {
        GroRunState.PROMOTED,
        GroRunState.PARTIAL_RESULT,
        GroRunState.FAILED,
        GroRunState.CANCELLED,
        GroRunState.TIMED_OUT,
        GroRunState.BLOCKED,
    }
)

_UNWIND = frozenset({GroRunState.CANCELLED, GroRunState.FAILED, GroRunState.TIMED_OUT, GroRunState.BLOCKED})

#: The closed transition table. An entry outside this table raises
#: ``IllegalGroTransition``; there is no silent repair.
_TRANSITIONS: dict[GroRunState, frozenset[GroRunState]] = {
    GroRunState.RECEIVED: frozenset({GroRunState.NORMALIZED, GroRunState.CANCELLED, GroRunState.FAILED}),
    GroRunState.NORMALIZED: frozenset({GroRunState.POLICY_DECIDED, GroRunState.CANCELLED, GroRunState.FAILED}),
    GroRunState.POLICY_DECIDED: frozenset({GroRunState.GROUP_CREATED, GroRunState.SINGLE_ATTEMPT, GroRunState.NEEDS_HUMAN, GroRunState.CANCELLED, GroRunState.FAILED, GroRunState.BLOCKED}),
    GroRunState.GROUP_CREATED: frozenset({GroRunState.READY}) | _UNWIND,
    GroRunState.SINGLE_ATTEMPT: frozenset({GroRunState.READY}) | _UNWIND,
    GroRunState.READY: frozenset({GroRunState.RUNNING}) | _UNWIND,
    GroRunState.RUNNING: frozenset({GroRunState.VERIFYING}) | _UNWIND,
    GroRunState.VERIFYING: frozenset({GroRunState.RANKED, GroRunState.NO_ACCEPTABLE_CANDIDATE}) | _UNWIND,
    GroRunState.RANKED: frozenset({GroRunState.SELECTED, GroRunState.NO_ACCEPTABLE_CANDIDATE, GroRunState.CANCELLED, GroRunState.FAILED}),
    GroRunState.SELECTED: frozenset({GroRunState.PROMOTED, GroRunState.NO_ACCEPTABLE_CANDIDATE, GroRunState.PARTIAL_RESULT, GroRunState.CANCELLED, GroRunState.FAILED, GroRunState.TIMED_OUT}),
    GroRunState.NO_ACCEPTABLE_CANDIDATE: frozenset({GroRunState.REPAIR_GROUP, GroRunState.NEEDS_HUMAN, GroRunState.PARTIAL_RESULT, GroRunState.FAILED, GroRunState.CANCELLED}),
    GroRunState.REPAIR_GROUP: frozenset({GroRunState.READY, GroRunState.FAILED, GroRunState.CANCELLED}),
    GroRunState.NEEDS_HUMAN: frozenset({GroRunState.PARTIAL_RESULT, GroRunState.FAILED, GroRunState.CANCELLED}),
    GroRunState.PROMOTED: frozenset(),
    GroRunState.PARTIAL_RESULT: frozenset(),
    GroRunState.FAILED: frozenset(),
    GroRunState.CANCELLED: frozenset(),
    GroRunState.TIMED_OUT: frozenset(),
    GroRunState.BLOCKED: frozenset(),
}


def allowed_transitions(state: GroRunState) -> frozenset[GroRunState]:
    """Successor states legal from ``state`` (frozen copy of the table)."""

    return _TRANSITIONS[state]


def can_transition(frm: GroRunState, to: GroRunState) -> bool:
    return to in _TRANSITIONS[frm]


class CandidateStatus(StrEnum):
    """Execution status of one candidate lane.

    ``COMPLETED`` means the lane produced a final result — not that the result
    is correct; correctness is the verifier's question.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    NOT_STARTED = "not_started"


class GateStatus(StrEnum):
    """Hard-gate verdict. ``UNKNOWN`` is never collapsed into ``PASS``."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


def _bounded(text: str, limit: int = _BOUNDED_TEXT) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class TaskContract:
    """Normalized task contract (plan section 9, phase A/C)."""

    run_id: str
    goal: str
    task_type: TaskType
    risk_tier: RiskTier
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    task_id: str = ""
    policy_version: str = POLICY_VERSION
    evaluator_version: str = EVALUATOR_VERSION
    strategy_registry_version: str = STRATEGY_REGISTRY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "task_type": self.task_type.value,
            "risk_tier": self.risk_tier.value,
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "policy_version": self.policy_version,
            "evaluator_version": self.evaluator_version,
            "strategy_registry_version": self.strategy_registry_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContract:
        return cls(
            run_id=str(data.get("run_id", "")),
            task_id=str(data.get("task_id", "")),
            goal=str(data.get("goal", "")),
            task_type=TaskType(data.get("task_type", TaskType.GENERAL)),
            risk_tier=RiskTier(data.get("risk_tier", RiskTier.ORDINARY)),
            acceptance_criteria=tuple(str(c) for c in data.get("acceptance_criteria", ()) or ()),
            constraints=tuple(str(c) for c in data.get("constraints", ()) or ()),
            policy_version=str(data.get("policy_version", POLICY_VERSION)),
            evaluator_version=str(data.get("evaluator_version", EVALUATOR_VERSION)),
            strategy_registry_version=str(data.get("strategy_registry_version", STRATEGY_REGISTRY_VERSION)),
        )


@dataclass(frozen=True)
class GroBudget:
    """Bounds for one orchestration run. Bounds are refused, never clamped."""

    max_candidates: int = 3
    max_active: int = 2
    candidate_timeout_seconds: float = 240.0
    run_timeout_seconds: float = 900.0
    max_repair_rounds: int = 1

    def __post_init__(self) -> None:
        if self.max_candidates < 1:
            raise ValueError(f"max_candidates must be >= 1 (got {self.max_candidates})")
        if self.max_active < 1:
            raise ValueError(f"max_active must be >= 1 (got {self.max_active})")
        if self.max_active > self.max_candidates:
            raise ValueError(f"max_active ({self.max_active}) cannot exceed max_candidates ({self.max_candidates})")
        if self.candidate_timeout_seconds <= 0:
            raise ValueError("candidate_timeout_seconds must be > 0")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be > 0")
        if self.max_repair_rounds < 0:
            raise ValueError(f"max_repair_rounds must be >= 0 (got {self.max_repair_rounds})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_candidates": self.max_candidates,
            "max_active": self.max_active,
            "candidate_timeout_seconds": self.candidate_timeout_seconds,
            "run_timeout_seconds": self.run_timeout_seconds,
            "max_repair_rounds": self.max_repair_rounds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroBudget:
        return cls(
            max_candidates=int(data.get("max_candidates", 3)),
            max_active=int(data.get("max_active", 2)),
            candidate_timeout_seconds=float(data.get("candidate_timeout_seconds", 240.0)),
            run_timeout_seconds=float(data.get("run_timeout_seconds", 900.0)),
            max_repair_rounds=int(data.get("max_repair_rounds", 1)),
        )


@dataclass(frozen=True)
class StrategySpec:
    """One dealt strategy: a distinct approach to the same objective."""

    strategy_id: str
    task_type: TaskType
    approach: str
    signals: str = ""
    version: str = STRATEGY_REGISTRY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "task_type": self.task_type.value,
            "approach": self.approach,
            "signals": self.signals,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategySpec:
        return cls(
            strategy_id=str(data.get("strategy_id", "")),
            task_type=TaskType(data.get("task_type", TaskType.GENERAL)),
            approach=str(data.get("approach", "")),
            signals=str(data.get("signals", "")),
            version=str(data.get("version", STRATEGY_REGISTRY_VERSION)),
        )


@dataclass(frozen=True)
class CandidateSpec:
    """One candidate lane: which strategy attempts the contract, with what context."""

    candidate_id: str
    run_id: str
    strategy: StrategySpec
    #: The frozen contract's normalized goal, copied so a spec is self-contained.
    objective: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    attempt: int = 1
    repair_context: str = ""

    def to_prompt(self) -> str:
        """Render the candidate task prompt.

        The strategy approach is guidance for *how* to attempt the objective;
        the objective and acceptance criteria come verbatim from the frozen
        contract so every candidate answers the same question.
        """

        parts = [f"Objective: {self.objective}", f"Approach you must follow: {self.strategy.approach}"]
        if self.acceptance_criteria:
            parts.append("Acceptance criteria (all must hold; report evidence for each):")
            parts.extend(f"- {criterion}" for criterion in self.acceptance_criteria)
        if self.repair_context:
            parts.append("A previous attempt failed verification. Fix exactly the proven shortfall listed below; do not redo work that already verified:\n" + self.repair_context)
        parts.append("Deliver the actual result, not a plan to do it. Report failures explicitly.")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "strategy": self.strategy.to_dict(),
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "attempt": self.attempt,
            "repair_context": self.repair_context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateSpec:
        return cls(
            candidate_id=str(data.get("candidate_id", "")),
            run_id=str(data.get("run_id", "")),
            strategy=StrategySpec.from_dict(data.get("strategy", {}) or {}),
            objective=str(data.get("objective", "")),
            acceptance_criteria=tuple(str(c) for c in data.get("acceptance_criteria", ()) or ()),
            attempt=int(data.get("attempt", 1)),
            repair_context=str(data.get("repair_context", "")),
        )


@dataclass
class CandidateOutcome:
    """What one candidate lane produced (plan section 17 interface)."""

    candidate_id: str
    status: CandidateStatus
    output_text: str = ""
    artifacts: list[str] = field(default_factory=list)
    #: Measured provider usage when the provider reported it; ``None`` means
    #: unreported and is never fabricated as zero.
    usage: dict[str, int] | None = None
    duration_ms: float = 0.0
    error: str = ""
    bash_executions: list[dict[str, Any]] = field(default_factory=list)
    tool_receipt_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "output_text": _bounded(self.output_text),
            "artifacts": [str(a) for a in self.artifacts][:64],
            "usage": dict(self.usage) if self.usage else None,
            "duration_ms": self.duration_ms,
            "error": _bounded(self.error, 1000),
            "bash_execution_count": len(self.bash_executions),
            "tool_receipt_count": self.tool_receipt_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateOutcome:
        return cls(
            candidate_id=str(data.get("candidate_id", "")),
            status=CandidateStatus(data.get("status", CandidateStatus.FAILED)),
            output_text=str(data.get("output_text", "")),
            artifacts=[str(a) for a in data.get("artifacts", []) or []],
            usage=dict(data["usage"]) if isinstance(data.get("usage"), dict) else None,
            duration_ms=float(data.get("duration_ms", 0.0)),
            error=str(data.get("error", "")),
            bash_executions=[],
            tool_receipt_count=int(data.get("tool_receipt_count", 0)),
        )


@dataclass(frozen=True)
class GateResult:
    """One hard gate: ``pass`` / ``fail`` / ``unknown`` with a real reason.

    ``required`` separates the gates a contract demands from informational
    ones: eligibility is exactly "every *required* gate passed", so an
    unknown on a required gate keeps a candidate ineligible (unknown is never
    a pass) while a non-required gate cannot block anything.
    """

    name: str
    status: GateStatus
    detail: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "detail": _bounded(self.detail, 500), "required": self.required}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateResult:
        return cls(name=str(data.get("name", "")), status=GateStatus(data.get("status", GateStatus.UNKNOWN)), detail=str(data.get("detail", "")), required=bool(data.get("required", True)))


@dataclass
class CandidateEvaluation:
    """Verification + scoring record for one candidate (plan section 16.1)."""

    candidate_id: str
    evaluator_version: str
    gates: list[GateResult] = field(default_factory=list)
    eligible: bool = False
    absolute_score: float | None = None
    metric_breakdown: dict[str, float] = field(default_factory=dict)
    relative_rank: int | None = None
    relative_margin: float | None = None
    reason_codes: list[str] = field(default_factory=list)
    evidence_units: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "evaluator_version": self.evaluator_version,
            "gates": [gate.to_dict() for gate in self.gates],
            "eligible": self.eligible,
            "absolute_score": self.absolute_score,
            "metric_breakdown": {k: round(v, 6) for k, v in self.metric_breakdown.items()},
            "relative_rank": self.relative_rank,
            "relative_margin": self.relative_margin,
            "reason_codes": list(self.reason_codes),
            "evidence_units": self.evidence_units,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateEvaluation:
        return cls(
            candidate_id=str(data.get("candidate_id", "")),
            evaluator_version=str(data.get("evaluator_version", EVALUATOR_VERSION)),
            gates=[GateResult.from_dict(g) for g in data.get("gates", []) or []],
            eligible=bool(data.get("eligible", False)),
            absolute_score=None if data.get("absolute_score") is None else float(data["absolute_score"]),
            metric_breakdown={str(k): float(v) for k, v in (data.get("metric_breakdown") or {}).items()},
            relative_rank=None if data.get("relative_rank") is None else int(data["relative_rank"]),
            relative_margin=None if data.get("relative_margin") is None else float(data["relative_margin"]),
            reason_codes=[str(r) for r in data.get("reason_codes", []) or []],
            evidence_units=int(data.get("evidence_units", 0)),
        )


@dataclass
class SelectionResult:
    """Deterministic selection outcome; ``selected_candidate_id`` is ``None``
    when no candidate passed every required gate."""

    outcome: str  # "selected" | "no_acceptable_candidate" | "needs_human"
    selected_candidate_id: str | None = None
    reason: str = ""
    ranking: list[str] = field(default_factory=list)
    repairable: bool = False
    score_margin_over_runner_up: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "selected_candidate_id": self.selected_candidate_id,
            "reason": _bounded(self.reason, 1000),
            "ranking": list(self.ranking),
            "repairable": self.repairable,
            "score_margin_over_runner_up": self.score_margin_over_runner_up,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectionResult:
        return cls(
            outcome=str(data.get("outcome", "no_acceptable_candidate")),
            selected_candidate_id=data.get("selected_candidate_id"),
            reason=str(data.get("reason", "")),
            ranking=[str(r) for r in data.get("ranking", []) or []],
            repairable=bool(data.get("repairable", False)),
            score_margin_over_runner_up=None if data.get("score_margin_over_runner_up") is None else float(data["score_margin_over_runner_up"]),
        )


@dataclass
class GroRun:
    """The durable orchestration run: contract, candidates, evidence, verdicts."""

    run_id: str
    owner_id: str
    thread_id: str
    state: GroRunState
    mode: RunMode
    contract: TaskContract
    budget: GroBudget
    candidate_specs: list[CandidateSpec] = field(default_factory=list)
    outcomes: dict[str, CandidateOutcome] = field(default_factory=dict)
    evaluations: dict[str, CandidateEvaluation] = field(default_factory=dict)
    selection: SelectionResult | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    repair_round: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    save_error: str | None = None

    def transition(self, to: GroRunState, *, reason: str = "", actor: str = "runner") -> None:
        """Move the run to ``to`` or raise; every transition is journaled."""

        if not can_transition(self.state, to):
            raise IllegalGroTransition(f"illegal GRO run transition {self.state.value} -> {to.value} (reason: {reason or 'unstated'})")
        self.events.append(
            {
                "type": "state_transition",
                "from": self.state.value,
                "to": to.value,
                "reason": _bounded(reason, 300),
                "actor": actor,
                "at": time.time(),
            }
        )
        if len(self.events) > _MAX_EVENT_HISTORY:
            del self.events[: len(self.events) - _MAX_EVENT_HISTORY]
        self.state = to
        self.updated_at = time.time()

    def record_event(self, event_type: str, **fields: Any) -> None:
        """Append one bounded observability event to the run journal."""

        self.events.append({"type": _bounded(event_type, 100), "at": time.time(), **{k: _bounded(str(v), 300) for k, v in fields.items()}})
        if len(self.events) > _MAX_EVENT_HISTORY:
            del self.events[: len(self.events) - _MAX_EVENT_HISTORY]
        self.updated_at = time.time()

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @classmethod
    def new(
        cls,
        *,
        run_id: str | None,
        owner_id: str,
        thread_id: str,
        contract: TaskContract,
        budget: GroBudget,
        mode: RunMode,
    ) -> GroRun:
        return cls(
            run_id=run_id or f"gro_{uuid.uuid4().hex[:12]}",
            owner_id=owner_id,
            thread_id=thread_id,
            state=GroRunState.RECEIVED,
            mode=mode,
            contract=contract,
            budget=budget,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "run_id": self.run_id,
            "owner_id": self.owner_id,
            "thread_id": self.thread_id,
            "state": self.state.value,
            "mode": self.mode.value,
            "contract": self.contract.to_dict(),
            "budget": self.budget.to_dict(),
            "candidate_specs": [spec.to_dict() for spec in self.candidate_specs],
            "outcomes": {cid: outcome.to_dict() for cid, outcome in self.outcomes.items()},
            "evaluations": {cid: evaluation.to_dict() for cid, evaluation in self.evaluations.items()},
            "selection": self.selection.to_dict() if self.selection else None,
            "decision": self.decision,
            "repair_round": self.repair_round,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": list(self.events[-64:]),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroRun:
        run = cls(
            run_id=str(data.get("run_id", "")),
            owner_id=str(data.get("owner_id", "")),
            thread_id=str(data.get("thread_id", "")),
            state=GroRunState(data.get("state", GroRunState.FAILED)),
            mode=RunMode(data.get("mode", RunMode.SINGLE)),
            contract=TaskContract.from_dict(data.get("contract", {}) or {}),
            budget=GroBudget.from_dict(data.get("budget", {}) or {}),
        )
        run.candidate_specs = [CandidateSpec.from_dict(s) for s in data.get("candidate_specs", []) or []]
        run.outcomes = {str(cid): CandidateOutcome.from_dict(o) for cid, o in (data.get("outcomes") or {}).items()}
        run.evaluations = {str(cid): CandidateEvaluation.from_dict(e) for cid, e in (data.get("evaluations") or {}).items()}
        selection = data.get("selection")
        run.selection = SelectionResult.from_dict(selection) if selection else None
        run.decision = dict(data.get("decision") or {})
        run.repair_round = int(data.get("repair_round", 0))
        run.created_at = float(data.get("created_at", time.time()))
        run.updated_at = float(data.get("updated_at", time.time()))
        run.events = list(data.get("events") or [])
        return run
