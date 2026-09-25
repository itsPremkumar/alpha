"""``ResilienceConfig``: every knob in one pydantic model, default OFF.

The model is deliberately standalone: it is *not* wired into
``alpha.config.app_config.AppConfig`` by this package, because promoting a new
config section is a shared-config change reviewed by the central owner (the
same boundary ``alpha/memory/affective`` uses). A host injects
``ResilienceConfig()`` directly today; promotion into ``AppConfig`` is an
additive follow-up patch.

**Default OFF.** ``enabled`` is ``False``. With ``enabled=False`` every reader
method still returns a *valid, inert* component - the breakers admit
everything, the guards never intervene, budgets are unbounded - so wiring the
model in cannot change a host's behaviour before the operator opts in. The
single exception is :meth:`ResilienceConfig.recovery_defaults`, which hands
``enabled`` to :func:`~alpha.runtime.resilience.recovery.recover` so a disabled
config makes recovery a strict pass-through.

Every field has exactly one reader, and
``test_every_config_field_has_a_real_reader`` walks the model and asserts the
value reached the component that reads it. Adding a field without a reader
fails that test.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from alpha.runtime.resilience.budget import CostMeter, ResourceBudget
from alpha.runtime.resilience.circuit import CircuitBreaker
from alpha.runtime.resilience.clock import Clock
from alpha.runtime.resilience.convergence import ConvergenceGuard
from alpha.runtime.resilience.idempotency import IdempotencyBackend, IdempotencyRegistry
from alpha.runtime.resilience.retry import FullJitter, NoJitter, RetryPolicy

__all__ = [
    "BudgetSettings",
    "CircuitSettings",
    "ConvergenceSettings",
    "IdempotencySettings",
    "ResilienceConfig",
    "RetrySettings",
]


class _Frozen(BaseModel):
    """Base for the nested settings: strict, immutable, no surprise defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrySettings(_Frozen):
    """Bounded retry shape. ``attempts`` is a hard ceiling."""

    attempts: int = Field(default=3, ge=1, le=100, description="Total attempts, including the first. Hard ceiling.")
    base_delay_seconds: float = Field(default=0.1, ge=0.0, le=600.0, description="Delay before the first retry, in clock seconds.")
    multiplier: float = Field(default=2.0, ge=1.0, le=10.0, description="Exponential growth factor per attempt.")
    max_delay_seconds: float = Field(default=30.0, ge=0.0, le=3600.0, description="Cap applied to the nominal delay before the jitter draw.")
    jitter: str = Field(default="full", pattern="^(none|full)$", description="Jitter strategy: 'none' or 'full' (uniform in [0, delay]).")


class CircuitSettings(_Frozen):
    """Failure threshold and reset window for the breaker."""

    failure_threshold: int = Field(default=3, ge=1, le=1000, description="Consecutive failures that open the circuit.")
    reset_timeout_seconds: float = Field(default=30.0, gt=0.0, le=3600.0, description="Clock seconds an open circuit waits before the single half-open probe.")


class ConvergenceSettings(_Frozen):
    """Thrash detection thresholds and the hard caps that end a healing loop."""

    repeat_threshold: int = Field(default=3, ge=1, le=100, description="Identical actions in a row with no progress before the ladder fires.")
    ping_pong_window: int = Field(default=3, ge=3, le=100, description="Window (>=3) that must show an A->B->A state cycle.")
    no_progress_threshold: int = Field(default=3, ge=1, le=100, description="Consecutive no-progress observations before the ladder fires.")
    alternating_failure_window: int = Field(default=3, ge=3, le=100, description="Window (>=3) that must show alternating failure signatures.")
    max_attempts: int = Field(default=8, ge=1, le=1000, description="Hard cap on attempt-kind observations; exceeding it returns exhausted.")
    max_reflections: int = Field(default=2, ge=1, le=100, description="Hard cap on reflection-kind observations (context/evidence rungs).")
    max_replans: int = Field(default=2, ge=1, le=100, description="Hard cap on replan-kind observations (strategy/delegate rungs).")
    history_limit: int = Field(default=64, ge=8, le=4096, description="Bounded observation history kept for signal detection.")


class BudgetSettings(_Frozen):
    """Attempt / wall-clock / cost bounds. ``None`` means unbounded."""

    max_attempts: int | None = Field(default=None, ge=1, le=100_000, description="Total attempts across the whole recovery. None = unbounded.")
    wall_clock_seconds: float | None = Field(default=None, gt=0.0, le=86_400.0, description="Total wall-clock budget in clock seconds. None = unbounded.")
    max_cost: float | None = Field(default=None, ge=0.0, le=1e12, description="Total cost units allowed. None = unbounded.")


class IdempotencySettings(_Frozen):
    """How long a recorded outcome suppresses a repeated key."""

    ttl_seconds: float = Field(default=3600.0, gt=0.0, le=604_800.0, description="Retention of a key -> outcome record, measured on the injected clock.")


class ResilienceConfig(_Frozen):
    """Master switch plus every sub-setting. ``enabled`` defaults to False."""

    enabled: bool = Field(
        default=False,
        description="Master switch. False makes recover() a strict single-call pass-through and every component inert.",
    )
    retry: RetrySettings = Field(default_factory=RetrySettings, description="Bounded retry shape.")
    circuit: CircuitSettings = Field(default_factory=CircuitSettings, description="Circuit breaker thresholds.")
    convergence: ConvergenceSettings = Field(default_factory=ConvergenceSettings, description="Thrash detection and hard caps.")
    budget: BudgetSettings = Field(default_factory=BudgetSettings, description="Attempt / wall-clock / cost bounds.")
    idempotency: IdempotencySettings = Field(default_factory=IdempotencySettings, description="Outcome retention window.")

    # -- readers ----------------------------------------------------------

    def retry_policy(self) -> RetryPolicy:
        """Read ``retry`` into a :class:`~alpha.runtime.resilience.retry.RetryPolicy`."""
        jitter = FullJitter() if self.retry.jitter == "full" else NoJitter()
        return RetryPolicy(
            attempts=self.retry.attempts,
            base_delay=self.retry.base_delay_seconds,
            multiplier=self.retry.multiplier,
            max_delay=self.retry.max_delay_seconds,
            jitter=jitter,
        )

    def circuit_breaker(self, clock: Clock, *, name: str = "default") -> CircuitBreaker:
        """Read ``circuit`` into a :class:`~alpha.runtime.resilience.circuit.CircuitBreaker`."""
        return CircuitBreaker(
            name=name,
            failure_threshold=self.circuit.failure_threshold,
            reset_timeout=self.circuit.reset_timeout_seconds,
            clock=clock,
        )

    def convergence_guard(self) -> ConvergenceGuard:
        """Read ``convergence`` into a :class:`~alpha.runtime.resilience.convergence.ConvergenceGuard`."""
        return ConvergenceGuard(
            repeat_threshold=self.convergence.repeat_threshold,
            ping_pong_window=self.convergence.ping_pong_window,
            no_progress_threshold=self.convergence.no_progress_threshold,
            alternating_failure_window=self.convergence.alternating_failure_window,
            max_attempts=self.convergence.max_attempts,
            max_reflections=self.convergence.max_reflections,
            max_replans=self.convergence.max_replans,
            max_history=self.convergence.history_limit,
        )

    def resource_budget(self, clock: Clock, *, cost_meter: CostMeter | None = None, label: str = "resilience") -> ResourceBudget:
        """Read ``budget`` into a :class:`~alpha.runtime.resilience.budget.ResourceBudget`."""
        return ResourceBudget(
            clock=clock,
            max_attempts=self.budget.max_attempts,
            wall_clock_timeout=self.budget.wall_clock_seconds,
            max_cost=self.budget.max_cost,
            cost_meter=cost_meter,
            label=label,
        )

    def idempotency_registry(self, clock: Clock, *, backend: IdempotencyBackend | None = None) -> IdempotencyRegistry:
        """Read ``idempotency.ttl_seconds`` into a registry."""
        return IdempotencyRegistry(clock=clock, ttl=self.idempotency.ttl_seconds, backend=backend)

    def recovery_kwargs(self, clock: Clock, *, cost_meter: CostMeter | None = None) -> dict[str, object]:
        """Keyword arguments for :func:`~alpha.runtime.resilience.recovery.recover`.

        Builds every injected component from this config, and carries ``enabled``
        through so a disabled config makes recovery a pass-through. Explicitly
        passed components in a ``recover`` call still win over these defaults.
        """
        return {
            "enabled": self.enabled,
            "policy": self.retry_policy(),
            "breaker": self.circuit_breaker(clock, name="recovery"),
            "budget": self.resource_budget(clock, cost_meter=cost_meter),
            "guard": self.convergence_guard(),
        }
