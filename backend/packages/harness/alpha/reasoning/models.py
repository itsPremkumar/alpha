"""Structured control-plane records for Alpha's reasoning policy.

The records in this module describe what the reasoning plane *proposes* and
what it has been told by existing runtime owners.  They never own execution,
authorization, verification, or completion.  Every bounded scalar is clamped
into a documented range and the change is retained in ``clamp_disclosures`` so
a downstream renderer cannot present an out-of-range value as if the caller had
supplied it.

No field exists for provider-private reasoning tokens, an internal monologue,
or an unbounded chain log.  User-facing text is assembled separately by
:mod:`alpha.reasoning.summary`, which applies the section 24 persistence guard.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AtomicThought",
    "BudgetDimension",
    "BudgetSnapshot",
    "ClampedModel",
    "ContradictionRecord",
    "DecisionRecord",
    "EvidenceRecord",
    "EvidenceSourceType",
    "HypothesisKind",
    "HypothesisRecord",
    "HypothesisStatus",
    "ObservationRecord",
    "ObservationSource",
    "ReasoningMode",
    "ReasoningPhase",
    "ReasoningState",
    "ReasoningStatus",
    "ReasoningStrategy",
    "ReflectionFailureClass",
    "ReflectionRecord",
    "ReversibilityLevel",
    "RiskLevel",
    "StopReason",
    "UncertaintyCalibration",
    "UncertaintyDimension",
    "UncertaintyEstimate",
    "UncertaintyRecord",
    "VerificationRecord",
    "VerificationStatus",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ClampedModel(BaseModel):
    """Pydantic base with disclosed range clamping.

    Validation clamps rather than raising so an untrusted or stale caller can
    never widen an operating range.  Each adjustment is appended to
    ``clamp_disclosures`` instead of being silently normalized.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    clamp_disclosures: list[str] = Field(default_factory=list, max_length=64)

    def _append_clamp_disclosure(self, notice: str) -> None:
        disclosures = list(self.clamp_disclosures)
        if notice not in disclosures:
            disclosures.append(notice)
        object.__setattr__(self, "clamp_disclosures", disclosures[:64])

    def clamp_field(
        self,
        field_name: str,
        minimum: float,
        maximum: float,
        *,
        integer: bool = False,
    ) -> float | int:
        """Clamp one field and disclose the exact adjustment.

        Non-finite floats are mapped to the lower bound.  This keeps a corrupt
        telemetry sample from poisoning sorting or comparison logic while the
        disclosure preserves the fact that the original value was unusable.
        """

        raw: object = getattr(self, field_name)
        if integer:
            numeric = int(raw)
        elif isinstance(raw, (int, float)):
            numeric = float(raw)
            if not math.isfinite(numeric):
                numeric = minimum
        else:  # pragma: no cover - pydantic normally rejects non-numeric input
            numeric = minimum
        clamped = min(maximum, max(minimum, numeric))
        if integer:
            clamped = int(clamped)
        if clamped != raw:
            object.__setattr__(self, field_name, clamped)
            self._append_clamp_disclosure(f"{field_name}={raw!r} clamped to [{minimum}, {maximum}]")
        return clamped


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReversibilityLevel(StrEnum):
    REVERSIBLE = "reversible"
    PARTIALLY_REVERSIBLE = "partially_reversible"
    IRREVERSIBLE = "irreversible"


class ReasoningMode(StrEnum):
    NATIVE = "native"
    EXPLICIT = "explicit"
    HYBRID = "hybrid"
    ROUTED = "routed"


class ReasoningStrategy(StrEnum):
    UNSELECTED = "unselected"
    DIRECT_RESPONSE = "direct_response"
    EXPLICIT_REASONING = "explicit_reasoning"
    NATIVE_REASONING = "native_reasoning"
    HYBRID_REASONING = "hybrid_reasoning"
    RESEARCH = "research"
    CODE_FIRST = "code_first"
    DEEP_PLANNING = "deep_planning"
    HIGH_RISK_GATED = "high_risk_gated"
    SELF_IMPROVEMENT = "self_improvement"
    ROLLED_EXISTING_RUNTIME = "rolled_existing_runtime"


class ReasoningPhase(StrEnum):
    INTAKE = "intake"
    CLASSIFIED = "classified"
    PLANNED = "planned"
    ACTING = "acting"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    REFLECTING = "reflecting"
    REPLANNING = "replanning"
    BRANCHING = "branching"
    DELEGATING = "delegating"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETION_PENDING = "completion_pending"
    TERMINAL = "terminal"


class ReasoningStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class StopReason(StrEnum):
    COMPLETED_BY_RUNTIME = "completed_by_runtime"
    BUDGET_EXHAUSTED = "budget_exhausted"
    VERIFICATION_REQUIRED = "verification_required"
    USER_CANCELLED = "user_cancelled"
    BLOCKED = "blocked"
    FAILED = "failed"
    LOOP_GUARD = "loop_guard"
    POLICY_DISABLED = "policy_disabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


class BudgetDimension(StrEnum):
    ITERATIONS = "iterations"
    MODEL_CALLS = "model_calls"
    TOOL_CALLS = "tool_calls"
    SUBAGENTS = "subagents"
    BRANCHES = "branches"
    REPLANS = "replans"
    REFLECTIONS = "reflections"
    WEB_SEARCHES = "web_searches"
    FILE_READS = "file_reads"
    SHELL_COMMANDS = "shell_commands"
    WALL_CLOCK_SECONDS = "wall_clock_seconds"
    TOKENS = "tokens"


class UncertaintyDimension(StrEnum):
    FACTUAL = "factual"
    MODEL = "model"
    TOOL = "tool"
    ENVIRONMENT = "environment"
    PLAN = "plan"
    VERIFICATION = "verification"


class UncertaintyCalibration(StrEnum):
    CALIBRATED = "calibrated"
    HEURISTIC = "heuristic"
    UNAVAILABLE = "unavailable"


class UncertaintyEstimate(ClampedModel):
    """One uncertainty dimension, with calibration carried on the value itself.

    ``value`` is an ordinal uncertainty score in ``[0, 1]`` (higher means more
    uncertain), not a probability.  A value is only marked ``CALIBRATED`` when
    it names the evaluation that produced it.
    """

    value: float = Field(default=0.5, description="Ordinal uncertainty in [0, 1]; higher is more uncertain.")
    calibration_status: UncertaintyCalibration = Field(default=UncertaintyCalibration.HEURISTIC)
    method: str = Field(default="unspecified heuristic", min_length=1, max_length=256)
    calibration_ref: str | None = Field(default=None, max_length=512)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _validate_and_clamp(self) -> Self:
        self.clamp_field("value", 0.0, 1.0)
        if self.calibration_status is UncertaintyCalibration.CALIBRATED and not (self.calibration_ref or "").strip():
            raise ValueError("calibrated uncertainty requires calibration_ref naming the evaluation that produced it")
        return self


class UncertaintyRecord(ClampedModel):
    """Multi-dimensional uncertainty snapshot.

    A missing dimension is unavailable, not zero.  ``overall`` is optional and
    is never synthesized as a probability by this package.
    """

    overall: UncertaintyEstimate | None = None
    factual: UncertaintyEstimate | None = None
    model: UncertaintyEstimate | None = None
    tool: UncertaintyEstimate | None = None
    environment: UncertaintyEstimate | None = None
    plan: UncertaintyEstimate | None = None
    verification: UncertaintyEstimate | None = None
    method: str = Field(default="composite of declared dimensions", min_length=1, max_length=256)
    recorded_at: datetime = Field(default_factory=_utc_now)

    def available_dimensions(self) -> tuple[UncertaintyDimension, ...]:
        return tuple(dimension for dimension in UncertaintyDimension if getattr(self, dimension.value) is not None)

    def dominant_dimensions(self) -> tuple[tuple[UncertaintyDimension, UncertaintyEstimate], ...]:
        values = [(dimension, getattr(self, dimension.value)) for dimension in UncertaintyDimension]
        available = [(dimension, estimate) for dimension, estimate in values if estimate is not None]
        return tuple(sorted(available, key=lambda item: (-item[1].value, item[0].value)))


class BudgetSnapshot(ClampedModel):
    """Serializable projection of a hard budget ledger."""

    limits: dict[BudgetDimension, float] = Field(default_factory=dict)
    consumed: dict[BudgetDimension, float] = Field(default_factory=dict)
    remaining: dict[BudgetDimension, float] = Field(default_factory=dict)
    exhausted: list[BudgetDimension] = Field(default_factory=list, max_length=32)
    stop_reason: StopReason | None = None

    @model_validator(mode="before")
    @classmethod
    def _clamp_maps(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        disclosures = list(normalized.get("clamp_disclosures") or [])
        for field_name in ("limits", "consumed", "remaining"):
            raw_values = normalized.get(field_name) or {}
            if not isinstance(raw_values, dict):
                continue
            values = dict(raw_values)
            for dimension, raw in tuple(values.items()):
                numeric = float(raw)
                if not math.isfinite(numeric):
                    numeric = 0.0
                clamped = min(1e12, max(0.0, numeric))
                if clamped != numeric:
                    notice = f"{field_name}.{dimension}={numeric!r} clamped to [0.0, 1e12]"
                    if notice not in disclosures:
                        disclosures.append(notice)
                values[dimension] = clamped
            normalized[field_name] = values
        exhausted = normalized.get("exhausted") or []
        if isinstance(exhausted, list):
            normalized["exhausted"] = list(dict.fromkeys(exhausted))
        normalized["clamp_disclosures"] = disclosures[:64]
        return normalized


class HypothesisKind(StrEnum):
    SOLUTION = "solution"
    DIAGNOSIS = "diagnosis"
    PLAN = "plan"
    EXPLANATION = "explanation"
    RESEARCH_CLAIM = "research_claim"


class HypothesisStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    SUPERSEDED = "superseded"


class HypothesisRecord(ClampedModel):
    id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4000)
    kind: HypothesisKind = HypothesisKind.EXPLANATION
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    status: HypothesisStatus = HypothesisStatus.CANDIDATE
    confidence: UncertaintyEstimate | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class ObservationSource(StrEnum):
    TOOL = "tool"
    USER = "user"
    MODEL = "model"
    SYSTEM = "system"
    TEST = "test"


class ObservationRecord(ClampedModel):
    id: str = Field(min_length=1, max_length=128)
    source: ObservationSource
    tool_name: str | None = Field(default=None, max_length=256)
    input_digest: str | None = Field(default=None, max_length=256)
    output_digest: str | None = Field(default=None, max_length=256)
    summary: str = Field(min_length=1, max_length=4000)
    facts: list[str] = Field(default_factory=list, max_length=100)
    errors: list[str] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=_utc_now)


class EvidenceSourceType(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    RUNTIME = "runtime"
    TEST = "test"
    ARTIFACT = "artifact"
    USER = "user"
    TOOL = "tool"
    COMPILER = "compiler"
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    RUNTIME_METRIC = "runtime_metric"
    DATABASE = "database"
    OTHER_AGENT = "other_agent"


class EvidenceRecord(ClampedModel):
    id: str = Field(min_length=1, max_length=128)
    claim: str = Field(min_length=1, max_length=4000)
    source: str = Field(min_length=1, max_length=2000)
    source_type: EvidenceSourceType
    locator: str | None = Field(default=None, max_length=2000)
    verified: bool = False
    verification_method: str | None = Field(default=None, max_length=512)
    confidence: float = Field(default=0.5)
    confidence_is_calibrated: bool = False
    confidence_method: str = Field(default="disclosed heuristic", max_length=256)
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _clamp_confidence(self) -> Self:
        self.clamp_field("confidence", 0.0, 1.0)
        return self


class VerificationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    PENDING = "pending"


class VerificationRecord(ClampedModel):
    id: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=2000)
    method: str = Field(min_length=1, max_length=512)
    status: VerificationStatus
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    failure_reason: str | None = Field(default=None, max_length=2000)
    independent: bool = False
    created_at: datetime = Field(default_factory=_utc_now)


class DecisionRecord(ClampedModel):
    id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    selected: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=4000)
    alternatives: list[str] = Field(default_factory=list, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    risk: RiskLevel = RiskLevel.MEDIUM
    reversibility: ReversibilityLevel = ReversibilityLevel.REVERSIBLE
    expected_next_action: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=0.5)
    confidence_is_calibrated: bool = False
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _clamp_confidence(self) -> Self:
        self.clamp_field("confidence", 0.0, 1.0)
        return self


class ContradictionRecord(ClampedModel):
    class ResolutionStatus(StrEnum):
        OPEN = "open"
        RESOLVED = "resolved"
        UNRESOLVED = "unresolved"
        DISCLOSED = "disclosed"

    id: str = Field(min_length=1, max_length=128)
    claim_a: str = Field(min_length=1, max_length=4000)
    claim_b: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    resolution: ResolutionStatus = ResolutionStatus.OPEN
    resolution_reason: str | None = Field(default=None, max_length=4000)
    created_at: datetime = Field(default_factory=_utc_now)


class ReflectionFailureClass(StrEnum):
    PLAN_WRONG = "plan_wrong"
    TOOL_WRONG = "tool_wrong"
    ASSUMPTION_WRONG = "assumption_wrong"
    MODEL_INSUFFICIENT = "model_insufficient"
    ENVIRONMENT_BROKEN = "environment_broken"
    VERIFIER_WRONG = "verifier_wrong"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOOP_DETECTED = "loop_detected"
    CONTRADICTION = "contradiction"
    UNKNOWN = "unknown"


class ReflectionRecord(ClampedModel):
    id: str = Field(min_length=1, max_length=128)
    failure_class: ReflectionFailureClass
    what_failed: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    lesson: str = Field(min_length=1, max_length=4000)
    next_change: str = Field(min_length=1, max_length=2000)
    should_persist_to_memory: bool = False
    confidence: float = Field(default=0.5)
    confidence_is_calibrated: bool = False
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _clamp_confidence(self) -> Self:
        self.clamp_field("confidence", 0.0, 1.0)
        return self


class AtomicThought(ClampedModel):
    """One dependency node in the Atom-of-Thoughts substrate."""

    id: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="question", min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=4000)
    statement: str = Field(min_length=1, max_length=16000)
    dependency_ids: list[str] = Field(default_factory=list, max_length=200)
    answer_equivalence_key: str = Field(min_length=1, max_length=128)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    confidence: UncertaintyEstimate = Field(default_factory=lambda: UncertaintyEstimate(value=0.5, method="neutral disclosed prior"))
    token_estimate: int = Field(default=1)

    @model_validator(mode="after")
    def _clamp_token_estimate(self) -> Self:
        self.clamp_field("token_estimate", 1, 1_000_000, integer=True)
        return self


class ReasoningState(ClampedModel):
    """Structured cognitive control state for one reasoning-plane run.

    ``messages`` remain conversation/model input.  This object is the
    structured control projection consumed by host middlewares, verification
    adapters, and the user-facing summary generator.  The package never
    transitions it to ``COMPLETED`` on its own.
    """

    # Timestamp alone is not a uniqueness key: two reasoning states minted in
    # the same microsecond (parallel subagents, replayed checkpoints) would
    # collide as identifiers. The suffix stays human-sortable, the uuid tail
    # keeps it collision-free.
    run_id: str = Field(
        default_factory=lambda: "reasoning-" + _utc_now().strftime("%Y%m%d%H%M%S%f") + "-" + uuid4().hex[:8],
        min_length=1,
        max_length=128,
    )
    mission_id: str | None = Field(default=None, max_length=128)
    parent_reasoning_id: str | None = Field(default=None, max_length=128)

    objective: str = Field(min_length=1, max_length=8000)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    non_goals: list[str] = Field(default_factory=list, max_length=100)

    strategy: ReasoningStrategy = ReasoningStrategy.UNSELECTED
    mode: ReasoningMode = ReasoningMode.EXPLICIT
    phase: ReasoningPhase = ReasoningPhase.INTAKE

    plan: list[str] = Field(default_factory=list, max_length=200)
    active_step: str | None = Field(default=None, max_length=2000)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=200)

    hypotheses: list[HypothesisRecord] = Field(default_factory=list, max_length=1000)
    selected_hypothesis_id: str | None = Field(default=None, max_length=128)
    observations: list[ObservationRecord] = Field(default_factory=list, max_length=1000)
    evidence: list[EvidenceRecord] = Field(default_factory=list, max_length=1000)
    verifications: list[VerificationRecord] = Field(default_factory=list, max_length=1000)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=200)
    assumptions: list[str] = Field(default_factory=list, max_length=200)
    contradictions: list[ContradictionRecord] = Field(default_factory=list, max_length=1000)
    reflection_events: list[ReflectionRecord] = Field(default_factory=list, max_length=1000)

    retry_count: int = Field(default=0)
    replan_count: int = Field(default=0)
    branch_count: int = Field(default=0)

    confidence: UncertaintyEstimate | None = None
    uncertainty: UncertaintyRecord | None = None
    budgets: BudgetSnapshot = Field(default_factory=BudgetSnapshot)

    reasoning_summary: list[str] = Field(default_factory=list, max_length=200)
    decision_log: list[DecisionRecord] = Field(default_factory=list, max_length=1000)
    artifact_provenance: list[str] = Field(default_factory=list, max_length=200)
    failure_reason: str | None = Field(default=None, max_length=4000)
    result: str | None = Field(default=None, max_length=8000)

    status: ReasoningStatus = ReasoningStatus.CREATED
    stop_reason: StopReason | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _clamp_counters(self) -> Self:
        self.clamp_field("retry_count", 0, 1_000_000, integer=True)
        self.clamp_field("replan_count", 0, 1_000_000, integer=True)
        self.clamp_field("branch_count", 0, 1_000_000, integer=True)
        return self
