"""Hard multi-dimensional reasoning budget with disclosed refusal.

This ledger is a local envelope beneath Alpha's existing parent, subagent,
workflow, sandbox, and run budgets.  It can refuse work; it cannot grant work,
raise an existing limit, or execute a tool.  Exhaustion is recorded with a
``BUDGET_EXHAUSTED`` stop reason rather than a silent truncation.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.reasoning.models import BudgetDimension, BudgetSnapshot, ClampedModel, StopReason

__all__ = [
    "BudgetAdmission",
    "BudgetEntry",
    "BudgetExhaustedError",
    "BudgetLimits",
    "BudgetStatus",
    "MarginalValueDecision",
    "ReasoningBudgetLedger",
]


class BudgetLimits(ClampedModel):
    """Hard limits for every reasoning budget dimension."""

    max_iterations: int = Field(default=12)
    max_model_calls: int = Field(default=12)
    max_tool_calls: int = Field(default=30)
    max_subagents: int = Field(default=4)
    max_branches: int = Field(default=2)
    max_replans: int = Field(default=3)
    max_reflections: int = Field(default=3)
    max_web_searches: int = Field(default=6)
    max_file_reads: int = Field(default=20)
    max_shell_commands: int = Field(default=8)
    max_wall_clock_seconds: float = Field(default=300.0)
    max_tokens: int = Field(default=32_000)

    @model_validator(mode="after")
    def _clamp_values(self) -> Self:
        for name in (
            "max_iterations",
            "max_model_calls",
            "max_tool_calls",
            "max_subagents",
            "max_branches",
            "max_replans",
            "max_reflections",
            "max_web_searches",
            "max_file_reads",
            "max_shell_commands",
            "max_tokens",
        ):
            self.clamp_field(name, 0, 1_000_000, integer=True)
        self.clamp_field("max_wall_clock_seconds", 0.0, 604_800.0)
        return self

    def limit_for(self, dimension: BudgetDimension) -> float:
        return float(getattr(self, f"max_{dimension.value}"))

    def as_mapping(self) -> dict[BudgetDimension, float]:
        return {dimension: self.limit_for(dimension) for dimension in BudgetDimension}


class BudgetEntry(BaseModel):
    timestamp: float
    dimension: BudgetDimension
    amount: float
    accepted: bool
    reason_code: str = Field(default="unspecified", pattern=r"^[a-z0-9_.-]{1,64}$")
    metadata: dict[str, str] = Field(default_factory=dict, max_length=32)

    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetAdmission(BaseModel):
    allowed: bool
    dimension: BudgetDimension | None
    requested: float
    remaining: float
    reason: str
    stop_reason: StopReason | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetStatus(BaseModel):
    exhausted: bool
    exhausted_dimensions: tuple[BudgetDimension, ...]
    stop_reason: StopReason | None
    remaining: dict[BudgetDimension, float]

    model_config = ConfigDict(extra="forbid", frozen=True)


class MarginalValueDecision(BaseModel):
    should_continue: bool
    expected_information_gain: float
    expected_cost: float
    expected_risk: float
    reason: str
    budget_available: bool = True

    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetExhaustedError(RuntimeError):
    """Raised by ``require`` when a caller demands already-exhausted budget."""

    def __init__(self, admission: BudgetAdmission) -> None:
        super().__init__(admission.reason)
        self.admission = admission


def _nonnegative_finite(value: float, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return numeric


class ReasoningBudgetLedger:
    """Thread-safe, instance-scoped hard budget ledger.

    ``consume`` and ``consume_many`` are the only mutating admission paths.
    A rejected request is still added to the accounting trail with the real
    reason; accepted and rejected attempts are never conflated.
    """

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self._clock = clock
        self._started_at = clock()
        self._used = {dimension: 0.0 for dimension in BudgetDimension}
        self._exhausted: set[BudgetDimension] = set()
        self._stop_reason: StopReason | None = None
        self._trail: list[BudgetEntry] = []
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, config: object) -> ReasoningBudgetLedger:
        """Build from a :class:`alpha.reasoning.config.ReasoningConfig`."""

        from alpha.reasoning.config import ReasoningConfig

        if not isinstance(config, ReasoningConfig):
            raise TypeError("config must be alpha.reasoning.config.ReasoningConfig")
        return cls(config.budget_defaults)

    def _elapsed_locked(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def _refresh_wall_clock_locked(self) -> None:
        elapsed = self._elapsed_locked()
        self._used[BudgetDimension.WALL_CLOCK_SECONDS] = max(self._used[BudgetDimension.WALL_CLOCK_SECONDS], elapsed)
        if elapsed >= self.limits.max_wall_clock_seconds:
            self._exhausted.add(BudgetDimension.WALL_CLOCK_SECONDS)
            self._stop_reason = StopReason.BUDGET_EXHAUSTED

    def _remaining_locked(self, dimension: BudgetDimension) -> float:
        self._refresh_wall_clock_locked()
        return max(0.0, self.limits.limit_for(dimension) - self._used[dimension])

    def remaining(self, dimension: BudgetDimension) -> float:
        with self._lock:
            return self._remaining_locked(dimension)

    def remaining_all(self) -> dict[BudgetDimension, float]:
        with self._lock:
            return {dimension: self._remaining_locked(dimension) for dimension in BudgetDimension}

    def _entry(
        self,
        *,
        dimension: BudgetDimension,
        amount: float,
        accepted: bool,
        reason_code: str,
        metadata: Mapping[str, str],
    ) -> BudgetEntry:
        return BudgetEntry(
            timestamp=self._clock(),
            dimension=dimension,
            amount=amount,
            accepted=accepted,
            reason_code=reason_code,
            metadata=dict(metadata),
        )

    def consume_many(
        self,
        amounts: Mapping[BudgetDimension, float],
        *,
        reason_code: str = "unspecified",
        metadata: Mapping[str, str] | None = None,
    ) -> BudgetAdmission:
        """Atomically admit a set of dimension charges.

        A multi-dimension reservation is all-or-nothing, which prevents a
        test-time-compute allocation from charging samples while silently
        failing to reserve its branch or token envelope.
        """

        if not amounts:
            raise ValueError("amounts must not be empty")
        normalized = {dimension: _nonnegative_finite(amount, dimension.value) for dimension, amount in amounts.items()}
        if all(amount == 0.0 for amount in normalized.values()):
            primary = next(iter(normalized))
            return BudgetAdmission(
                allowed=True,
                dimension=primary,
                requested=0.0,
                remaining=self.remaining(primary),
                reason="zero-cost reservation admitted",
            )
        clean_metadata = {str(key)[:64]: str(value)[:512] for key, value in (metadata or {}).items()}
        primary = max(normalized, key=lambda dimension: normalized[dimension])

        with self._lock:
            self._refresh_wall_clock_locked()
            exceeded: list[tuple[BudgetDimension, float, float]] = []
            for dimension, amount in normalized.items():
                if dimension in self._exhausted:
                    exceeded.append((dimension, amount, 0.0))
                    continue
                limit = self.limits.limit_for(dimension)
                projected = self._used[dimension] + amount
                if dimension is BudgetDimension.WALL_CLOCK_SECONDS:
                    projected = max(projected, self._elapsed_locked())
                if projected > limit:
                    exceeded.append((dimension, amount, max(0.0, limit - self._used[dimension])))

            if exceeded:
                for dimension, _, _ in exceeded:
                    self._exhausted.add(dimension)
                self._stop_reason = StopReason.BUDGET_EXHAUSTED
                first_dimension, _, first_remaining = exceeded[0]
                self._trail.append(
                    self._entry(
                        dimension=first_dimension,
                        amount=normalized[first_dimension],
                        accepted=False,
                        reason_code=reason_code,
                        metadata=clean_metadata,
                    )
                )
                return BudgetAdmission(
                    allowed=False,
                    dimension=first_dimension,
                    requested=normalized[first_dimension],
                    remaining=first_remaining,
                    reason=(f"budget refused {first_dimension.value}: requested={normalized[first_dimension]}, remaining={first_remaining}, limit={self.limits.limit_for(first_dimension)}"),
                    stop_reason=StopReason.BUDGET_EXHAUSTED,
                )

            for dimension, amount in normalized.items():
                self._used[dimension] += amount
                if self._used[dimension] >= self.limits.limit_for(dimension):
                    self._exhausted.add(dimension)
                    self._stop_reason = StopReason.BUDGET_EXHAUSTED
                self._trail.append(
                    self._entry(
                        dimension=dimension,
                        amount=amount,
                        accepted=True,
                        reason_code=reason_code,
                        metadata=clean_metadata,
                    )
                )
            return BudgetAdmission(
                allowed=True,
                dimension=primary,
                requested=normalized[primary],
                remaining=self._remaining_locked(primary),
                reason=f"admitted {len(normalized)} budget dimension(s)",
            )

    def consume(
        self,
        dimension: BudgetDimension,
        amount: float = 1.0,
        *,
        reason_code: str = "unspecified",
        metadata: Mapping[str, str] | None = None,
    ) -> BudgetAdmission:
        return self.consume_many({dimension: amount}, reason_code=reason_code, metadata=metadata)

    def require(
        self,
        dimension: BudgetDimension,
        amount: float = 1.0,
        *,
        reason_code: str = "unspecified",
    ) -> BudgetAdmission:
        admission = self.consume(dimension, amount, reason_code=reason_code)
        if not admission.allowed:
            raise BudgetExhaustedError(admission)
        return admission

    def marginal_value(
        self,
        *,
        expected_information_gain: float,
        expected_cost: float,
        expected_risk: float = 0.0,
    ) -> MarginalValueDecision:
        """Apply the plan's continue-only-on-positive-value rule.

        Values are caller-declared normalized units, not probabilities.  The
        function is pure with respect to budget; ``continue_if_value`` adds the
        available model-call check.
        """

        gain = _nonnegative_finite(expected_information_gain, "expected_information_gain")
        cost = _nonnegative_finite(expected_cost, "expected_cost")
        risk = _nonnegative_finite(expected_risk, "expected_risk")
        should_continue = gain > cost + risk
        if should_continue:
            reason = f"expected gain {gain} exceeds expected cost+risk {cost + risk}"
        else:
            reason = f"expected gain {gain} does not exceed expected cost+risk {cost + risk}"
        return MarginalValueDecision(
            should_continue=should_continue,
            expected_information_gain=gain,
            expected_cost=cost,
            expected_risk=risk,
            reason=reason,
        )

    def continue_if_value(
        self,
        *,
        expected_information_gain: float,
        expected_cost: float,
        expected_risk: float = 0.0,
        dimension: BudgetDimension = BudgetDimension.MODEL_CALLS,
    ) -> MarginalValueDecision:
        decision = self.marginal_value(
            expected_information_gain=expected_information_gain,
            expected_cost=expected_cost,
            expected_risk=expected_risk,
        )
        available = self.remaining(dimension) > 0.0
        if not available:
            return decision.model_copy(
                update={
                    "should_continue": False,
                    "budget_available": False,
                    "reason": f"{decision.reason}; {dimension.value} budget is exhausted",
                }
            )
        return decision

    def status(self) -> BudgetStatus:
        with self._lock:
            self._refresh_wall_clock_locked()
            return BudgetStatus(
                exhausted=bool(self._exhausted),
                exhausted_dimensions=tuple(sorted(self._exhausted, key=lambda dimension: dimension.value)),
                stop_reason=self._stop_reason,
                remaining=self.remaining_all(),
            )

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            self._refresh_wall_clock_locked()
            limits = self.limits.as_mapping()
            consumed = dict(self._used)
            remaining = {dimension: max(0.0, limits[dimension] - consumed[dimension]) for dimension in BudgetDimension}
            return BudgetSnapshot(
                limits=limits,
                consumed=consumed,
                remaining=remaining,
                exhausted=sorted(self._exhausted, key=lambda dimension: dimension.value),
                stop_reason=self._stop_reason,
            )

    @property
    def accounting_trail(self) -> tuple[BudgetEntry, ...]:
        with self._lock:
            return tuple(self._trail)
