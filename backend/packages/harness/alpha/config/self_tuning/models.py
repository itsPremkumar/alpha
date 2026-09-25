"""Closed protocol records for proposals, evidence, and outcomes.

The record types deliberately distinguish *proposal* from *permission*.  They
are based on the auditability principles in Google SRE's *Release Engineering*
and *Canarying Releases*: a candidate, its observations, its activation, and
its verification are separate immutable facts.  The split between a rollout
flag and a decision to roll out follows Martin Fowler's *Feature Toggles (Kill
Switches)*: possessing a candidate never grants authority to activate it.

All timestamps come from an injected :class:`Clock`; this module never reads
wall-clock time implicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Clock(Protocol):
    """Time source used by every state transition and observation window."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait for *seconds* according to the injected time source."""
        ...


class ManualClock:
    """Deterministic clock for offline orchestration and tests."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware start")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        """Return the current deterministic instant."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """Advance without real sleeping."""
        if seconds < 0:
            raise ValueError("cannot sleep for a negative duration")
        self._now += timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        """Explicit alias for advancing a deterministic clock."""
        self.sleep(seconds)


class UTCClock:
    """Minimal production clock.  It sleeps only when a caller explicitly asks."""

    def now(self) -> datetime:
        """Return the current UTC instant."""
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        """Sleep for a positive bounded duration."""
        if seconds <= 0:
            raise ValueError("UTCClock.sleep requires a positive duration")
        import time

        time.sleep(seconds)


class BlastRadius(StrEnum):
    """Declared scope of a configuration target."""

    INSTANCE = "instance"
    PROCESS = "process"
    DEPLOYMENT = "deployment"


class BlastRadiusClass(StrEnum):
    """Closed validation classification for one or more changes."""

    NOT_ASSESSED = "not_assessed"
    COSMETIC = "cosmetic"
    TUNABLE = "tunable"
    STRUCTURAL = "structural"


class ChangeKind(StrEnum):
    """Closed proposal kinds."""

    TUNE = "tune"
    FEATURE_FLAG = "feature_flag"
    RESOURCE_BUDGET = "resource_budget"
    ROLLBACK = "rollback"


class ChangeStatus(StrEnum):
    """Closed lifecycle vocabulary for a :class:`ChangeSet`."""

    PROPOSED = "proposed"
    VALIDATED = "validated"
    CANARY_RUNNING = "canary_running"
    CANARY_ABORTED = "canary_aborted"
    READY_TO_APPLY = "ready_to_apply"
    APPLIED = "applied"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"
    NO_OP = "no_op"


class CanaryStatus(StrEnum):
    """Closed canary evidence states."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNAVAILABLE = "unavailable"
    NOT_RUN = "not_run"


class CanaryDecision(StrEnum):
    """Closed canary decisions.  Only healthy evidence can promote."""

    PROMOTE = "promote"
    ABORT = "abort"


class VerificationStatus(StrEnum):
    """Closed post-apply verification states."""

    HEALTHY = "healthy"
    REGRESSED = "regressed"
    UNAVAILABLE = "unavailable"
    NOT_RUN = "not_run"


class ValidationErrorCode(StrEnum):
    """Closed validation failure vocabulary."""

    STALE_PREVIOUS_VALUE = "stale_previous_value"
    INVALID_DOMAIN = "invalid_domain"
    OUTSIDE_BOUNDS = "outside_bounds"
    OWNER_SCHEMA_INVALID = "owner_schema_invalid"
    FULL_CONFIG_INVALID = "full_config_invalid"
    NOT_REVERSIBLE = "not_reversible"
    ROLLBACK_VALUE_MISMATCH = "rollback_value_mismatch"
    BLAST_RADIUS_MISMATCH = "blast_radius_mismatch"
    VALIDATION_UNAVAILABLE = "validation_unavailable"


class ValidationWarningCode(StrEnum):
    """Closed non-fatal validation vocabulary."""

    CLOSE_TO_BOUND = "close_to_bound"
    HIGH_BLAST_RADIUS = "high_blast_radius"
    SCOPED_CANARY_REQUIRED = "scoped_canary_required"


class RefusalCode(StrEnum):
    """Closed refusal vocabulary, kept separate from route-around validation."""

    UNDECLARED_PATH = "undeclared_path"
    PROTECTED_PATH = "protected_path"
    OPERATOR_AUTHORIZATION_REQUIRED = "operator_authorization_required"
    OPERATOR_AUTHORIZATION_UNVERIFIABLE = "operator_authorization_unverifiable"
    OPERATOR_AUTHORIZATION_DENIED = "operator_authorization_denied"


class ProvenanceAction(StrEnum):
    """Closed append-only audit actions."""

    PROPOSE = "propose"
    VALIDATE = "validate"
    CANARY = "canary"
    APPLY = "apply"
    VERIFY = "verify"
    ROLLBACK = "rollback"
    CONFIRM = "confirm"
    RECOVER = "recover"


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return value


class ConfigChange(BaseModel):
    """One proposed dotted-path value plus its complete reversal evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str | None = Field(default=None, description="Dotted config path, mutually exclusive with key.")
    key: str | None = Field(default=None, description="Single key path, mutually exclusive with path.")
    proposed_value: Any = Field(description="Value proposed for the target.")
    previous_value: Any = Field(description="Exact value observed when the proposal was made.")
    rationale: str = Field(min_length=1, description="Why the change is needed.")
    author: str = Field(min_length=1, description="Proposal author/agent identity.")
    created_at: datetime = Field(description="Time read from the injected clock.")
    expected_effect: str = Field(min_length=1, description="Measurable expected effect.")
    blast_radius: BlastRadius = Field(description="Declared target blast radius.")
    reversible: bool = Field(description="Whether an exact rollback value is available.")
    rollback_value: Any = Field(description="Value to restore if retention is not earned.")
    operator_authorization_token: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Opaque operator-issued evidence for a product decision; this package neither mints nor validates it without an injected authority.",
    )

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        """Require exactly one path/key and a timezone-aware creation time."""
        if (self.path is None) == (self.key is None):
            raise ValueError("ConfigChange requires exactly one of path or key")
        candidate = self.path if self.path is not None else self.key
        if candidate is None or not candidate.strip() or candidate.strip() != candidate:
            raise ValueError("ConfigChange path/key must be non-blank and already normalized")
        if ".." in candidate.split("."):
            raise ValueError(f"ConfigChange path/key contains an empty segment: {candidate!r}")
        _aware_datetime(self.created_at)
        return self

    @property
    def target_path(self) -> str:
        """Return the canonical dotted target path."""
        return self.path if self.path is not None else str(self.key)

    @classmethod
    def create(
        cls,
        *,
        clock: Clock,
        path: str | None = None,
        key: str | None = None,
        proposed_value: Any,
        previous_value: Any,
        rationale: str,
        author: str,
        expected_effect: str,
        blast_radius: BlastRadius,
        reversible: bool = True,
        rollback_value: Any | None = None,
        operator_authorization_token: str | None = None,
    ) -> Self:
        """Build a change using only the injected clock."""
        return cls(
            path=path,
            key=key,
            proposed_value=proposed_value,
            previous_value=previous_value,
            rationale=rationale,
            author=author,
            created_at=clock.now(),
            expected_effect=expected_effect,
            blast_radius=blast_radius,
            reversible=reversible,
            rollback_value=previous_value if rollback_value is None else rollback_value,
            operator_authorization_token=operator_authorization_token,
        )


class ChangeSet(BaseModel):
    """An immutable group of changes moving through the same state machine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, description="Stable caller-provided proposal identifier.")
    changes: tuple[ConfigChange, ...] = Field(min_length=1, description="Atomic proposed changes.")
    author: str = Field(min_length=1, description="Proposal author/agent identity.")
    kind: ChangeKind = Field(description="Closed proposal kind.")
    status: ChangeStatus = Field(default=ChangeStatus.PROPOSED, description="Current lifecycle state.")
    created_at: datetime = Field(description="Time read from the injected clock.")

    @model_validator(mode="after")
    def _validate_changes(self) -> Self:
        """Reject duplicate target paths before any stage can observe them."""
        _aware_datetime(self.created_at)
        paths = [change.target_path for change in self.changes]
        duplicates = sorted({path for path in paths if paths.count(path) > 1})
        if duplicates:
            raise ValueError(f"ChangeSet contains duplicate target paths: {duplicates}")
        return self

    @classmethod
    def create(
        cls,
        *,
        clock: Clock,
        id: str,
        changes: tuple[ConfigChange, ...],
        author: str,
        kind: ChangeKind,
    ) -> Self:
        """Build a proposal using only the injected clock."""
        return cls(id=id, changes=changes, author=author, kind=kind, created_at=clock.now())


class ValidationIssue(BaseModel):
    """One validation diagnostic with a closed machine-readable code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ValidationErrorCode
    path: str
    message: str


class ValidationWarning(BaseModel):
    """One non-fatal validation diagnostic with a closed machine-readable code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ValidationWarningCode
    path: str
    message: str


class Refusal(BaseModel):
    """A disclosed allowlist/authority refusal, never a bypassable warning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: RefusalCode
    path: str
    reason: str


class ValidationResult(BaseModel):
    """Outcome of all pre-write checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationWarning, ...] = ()
    refusals: tuple[Refusal, ...] = ()
    blast_radius_class: BlastRadiusClass = BlastRadiusClass.NOT_ASSESSED
    no_op: bool = False
    reason: str | None = None

    @model_validator(mode="after")
    def _honest_outcome(self) -> Self:
        """A successful result cannot carry errors or refusals."""
        if self.ok and (self.errors or self.refusals):
            raise ValueError("ok ValidationResult cannot contain errors or refusals")
        if self.no_op and self.ok:
            raise ValueError("a no-op validation must not report ok")
        return self


class CanaryObservation(BaseModel):
    """One real or explicitly unavailable canary probe result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CanaryStatus
    metrics: dict[str, float] = Field(default_factory=dict)
    detail: str | None = None

    @model_validator(mode="after")
    def _finite_metrics(self) -> Self:
        """Reject synthetic/non-real metric values in probe evidence."""
        import math

        for name, value in self.metrics.items():
            if not name.strip() or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"canary metric {name!r} must have a finite real value")
        return self


class CanaryResult(BaseModel):
    """Canary decision; ``PROMOTE`` requires a real healthy observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_set_id: str
    decision: CanaryDecision
    status: CanaryStatus
    scope: str | None = None
    reason: str
    observed_at: datetime | None = None
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cannot_promote_without_health(self) -> Self:
        """Enforce the evidence invariant at the model boundary."""
        _aware_datetime(self.observed_at) if self.observed_at is not None else None
        if self.decision is CanaryDecision.PROMOTE and self.status is not CanaryStatus.HEALTHY:
            raise ValueError("canary cannot promote without healthy probe evidence")
        return self


class HealthSnapshot(BaseModel):
    """Finite numeric health evidence captured at one instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    captured_at: datetime
    metrics: dict[str, float]

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        """Reject blank names and non-real/non-finite values."""
        _aware_datetime(self.captured_at)
        for name, value in self.metrics.items():
            if not name.strip():
                raise ValueError("health metric names must not be blank")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"health metric {name!r} must be a finite real number, got {value!r}")
        return self


class VerificationResult(BaseModel):
    """Post-apply health comparison.  Only real non-regressing data may keep."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_set_id: str
    status: VerificationStatus
    keep: bool
    reason: str
    before: HealthSnapshot | None = None
    after: HealthSnapshot | None = None

    @model_validator(mode="after")
    def _honest_verification(self) -> Self:
        """No healthy/keep claim without a real post-change snapshot."""
        if self.keep != (self.status is VerificationStatus.HEALTHY):
            raise ValueError("keep must exactly match healthy verification status")
        if self.keep and (self.before is None or self.after is None):
            raise ValueError("healthy verification requires real before and after health snapshots")
        return self


class ApplyOutcome(StrEnum):
    """Closed atomic-application/protocol outcome set."""

    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    NO_OP = "no_op"
    NEEDS_HUMAN = "needs_human"


class ApplyResult(BaseModel):
    """Atomic application result; verified reload is explicit and mandatory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_set_id: str
    status: ApplyOutcome
    reason: str
    at: datetime
    verified_reload: bool = False

    @model_validator(mode="after")
    def _no_unverified_apply(self) -> Self:
        """An applied result must have a verified reload; verified cannot mean no-op."""
        _aware_datetime(self.at)
        if self.status is ApplyOutcome.APPLIED and not self.verified_reload:
            raise ValueError("applied requires verified_reload=True")
        if self.status is not ApplyOutcome.APPLIED and self.verified_reload:
            raise ValueError("verified_reload is only valid for applied")
        return self


class ProvenanceEntry(BaseModel):
    """One hash-chained JSONL audit fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    action: ProvenanceAction
    change_set_id: str
    path: str | None = None
    before: Any = None
    after: Any = None
    author: str
    rationale: str
    at: datetime
    status: str
    previous_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    entry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProtocolRun(BaseModel):
    """Complete terminal result for one propose-to-verification run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_set_id: str
    status: ApplyOutcome
    reason: str
    validation: ValidationResult
    canary: CanaryResult
    apply: ApplyResult
    verification: VerificationResult
    rollback: ApplyResult | None = None
