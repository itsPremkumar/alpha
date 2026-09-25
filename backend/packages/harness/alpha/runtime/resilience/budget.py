"""A small, honest resource budget: attempts, wall-clock, and an injected cost.

Three bounds, one object, fail-closed semantics:

* ``max_attempts`` - how many times the operation may start.
* ``wall_clock_timeout`` - a :class:`~alpha.runtime.resilience.clock.Deadline`
  on the injected clock, so it expires deterministically under test.
* ``max_cost`` - an abstract unit (cents, tool calls, tokens, whatever the host
  defines) measured through an injected :class:`CostMeter`. The kit never
  invents a pricing model.

**Refuse, never overshoot silently.** :meth:`ResourceBudget.consume` raises
:class:`~alpha.runtime.resilience.errors.BudgetExhaustedError` *before* the
work is done, so a caller that reserves first cannot start an operation it
cannot pay for. :meth:`ResourceBudget.record` is the post-hoc accounting path
for work already performed; it clamps to the cap and reports
``cost_clamped=True`` in the snapshot rather than quietly overrunning the
budget - an overshoot that nobody is told about is how a host burns a year of
quota in an afternoon.

The cost meter is a :class:`CostMeter` (a callable protocol), so a host can
meter whatever it cares about without this module knowing about money.

References (paraphrased, nothing copied):
* Beyer et al., *Site Reliability Engineering* (O'Reilly, 2016), ch. 4: an
  error budget that is not enforced is not a budget; exhaustion must be a
  first-class, visible outcome.
* Beyer et al., *Handling Overload* (HotOS '16): when a queue exceeds its
  budget the correct behaviour is to shed load early and say so, not to run the
  work and report success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from alpha.runtime.resilience.clock import Clock, Deadline, coerce_clock, require_delay
from alpha.runtime.resilience.errors import BudgetExhaustedError

__all__ = ["BudgetSnapshot", "CostMeter", "FixedCostMeter", "ResourceBudget", "ZeroCostMeter", "BudgetExhaustedError"]


@runtime_checkable
class CostMeter(Protocol):
    """Prices one metered step. The kit defines no units of its own."""

    def cost(self, label: str) -> float:
        """Cost of the next step called ``label``."""


@dataclass(frozen=True, slots=True)
class ZeroCostMeter:
    """Meter that charges nothing (the default; a budget of cost only)."""

    def cost(self, label: str) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class FixedCostMeter:
    """Meter that charges a constant amount per step."""

    amount: float

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("FixedCostMeter.amount must be >= 0")

    def cost(self, label: str) -> float:
        return float(self.amount)


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """Point-in-time budget state. Every field is a disclosed number."""

    attempts_used: int
    max_attempts: int | None
    cost_used: float
    max_cost: float | None
    remaining_cost: float | None
    seconds_remaining: float | None
    cost_clamped: bool = False

    @property
    def attempts_remaining(self) -> int | None:
        if self.max_attempts is None:
            return None
        return max(0, self.max_attempts - self.attempts_used)

    @property
    def exhausted(self) -> bool:
        """True when no further attempt or cost can be afforded."""
        if self.max_attempts is not None and self.attempts_used >= self.max_attempts:
            return True
        if self.max_cost is not None and self.cost_used >= self.max_cost:
            return True
        remaining = self.seconds_remaining
        return remaining is not None and remaining <= 0.0


class ResourceBudget:
    """Attempt / wall-clock / cost budget that refuses cleanly when spent.

    Per instance, no module-level singleton: two concurrent loops must be able
    to hold independent budgets. All time is read from the injected clock.
    """

    __slots__ = (
        "_attempts_used",
        "_clock",
        "_cost_clamped",
        "_cost_meter",
        "_cost_used",
        "_deadline",
        "_label",
        "_max_attempts",
        "_max_cost",
    )

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        max_attempts: int | None = None,
        wall_clock_timeout: float | None = None,
        max_cost: float | None = None,
        cost_meter: CostMeter | None = None,
        label: str = "budget",
    ) -> None:
        self._clock = coerce_clock(clock)
        if max_attempts is not None and int(max_attempts) < 1:
            raise ValueError("max_attempts must be >= 1 when set")
        if max_cost is not None:
            if float(max_cost) < 0:
                raise ValueError("max_cost must be >= 0 when set")
        if cost_meter is not None and not callable(getattr(cost_meter, "cost", None)):
            raise TypeError("cost_meter must expose cost(label)")
        self._max_attempts = int(max_attempts) if max_attempts is not None else None
        self._max_cost = float(max_cost) if max_cost is not None else None
        self._cost_meter: CostMeter = cost_meter if cost_meter is not None else ZeroCostMeter()
        self._label = label
        self._attempts_used = 0
        self._cost_used = 0.0
        self._cost_clamped = False
        if wall_clock_timeout is None:
            self._deadline: Deadline | None = None
        else:
            timeout = require_delay(wall_clock_timeout, name="wall_clock_timeout")
            self._deadline = Deadline.after(self._clock, timeout, label=label)

    # -- read-only views --------------------------------------------------

    @property
    def deadline(self) -> Deadline | None:
        """The wall-clock deadline, or ``None`` when unbounded."""
        return self._deadline

    @property
    def attempts_used(self) -> int:
        return self._attempts_used

    @property
    def cost_used(self) -> float:
        return self._cost_used

    @property
    def exhausted(self) -> bool:
        return self.snapshot().exhausted

    def snapshot(self) -> BudgetSnapshot:
        """Disclosed state of the budget right now."""
        return BudgetSnapshot(
            attempts_used=self._attempts_used,
            max_attempts=self._max_attempts,
            cost_used=self._cost_used,
            max_cost=self._max_cost,
            remaining_cost=None if self._max_cost is None else max(0.0, self._max_cost - self._cost_used),
            seconds_remaining=None if self._deadline is None else self._deadline.remaining(),
            cost_clamped=self._cost_clamped,
        )

    # -- spend ------------------------------------------------------------

    def _wall_clock_exhausted(self) -> bool:
        return self._deadline is not None and self._deadline.expired()

    def check(self) -> BudgetSnapshot:
        """Snapshot plus an early wall-clock refusal. Never raises.

        ``check`` is the cheap "can I still afford anything?" probe; it reports
        an exhausted wall clock without consuming an attempt.
        """
        return self.snapshot()

    def begin_attempt(self) -> int:
        """Reserve one attempt, or raise :class:`BudgetExhaustedError`.

        Call this *before* doing the work: an attempt that is refused must not
        have started a side effect.
        """
        if self._wall_clock_exhausted():
            raise BudgetExhaustedError(
                f"{self._label}: wall-clock budget exhausted",
                reason="wall_clock_budget_exhausted",
                used=self._deadline.remaining() if self._deadline else 0.0,
                limit=0.0,
            )
        if self._max_attempts is not None and self._attempts_used >= self._max_attempts:
            raise BudgetExhaustedError(
                f"{self._label}: attempt budget exhausted ({self._attempts_used}/{self._max_attempts})",
                reason="attempt_budget_exhausted",
                used=float(self._attempts_used),
                limit=float(self._max_attempts),
            )
        if self._max_cost is not None and self._cost_used >= self._max_cost:
            raise BudgetExhaustedError(
                f"{self._label}: cost budget exhausted ({self._cost_used}/{self._max_cost})",
                reason="cost_budget_exhausted",
                used=self._cost_used,
                limit=self._max_cost,
            )
        self._attempts_used += 1
        return self._attempts_used

    def consume(self, amount: float) -> float:
        """Pre-authorise ``amount`` of cost; raise if it does not fit.

        Returns the amount actually charged. Raising leaves the recorded cost
        untouched - a refused reservation is not a partial charge.
        """
        charge = require_delay(amount, name="amount")
        if self._max_cost is not None and self._cost_used + charge > self._max_cost:
            raise BudgetExhaustedError(
                f"{self._label}: cost {charge!r} would exceed the budget ({self._cost_used}+{charge} > {self._max_cost})",
                reason="cost_budget_exhausted",
                used=self._cost_used,
                limit=self._max_cost,
            )
        self._cost_used += charge
        return charge

    def consume_metered(self, label: str) -> float:
        """Price ``label`` through the injected meter and pre-authorise it."""
        return self.consume(self._cost_meter.cost(label))

    def record(self, amount: float) -> float:
        """Post-hoc accounting for work already done; clamps at the cap.

        Returns what was actually recorded. When the clamp bites,
        ``snapshot().cost_clamped`` is True: the overshoot is disclosed, not
        hidden.
        """
        charge = require_delay(amount, name="amount")
        if self._max_cost is not None and self._cost_used + charge > self._max_cost:
            self._cost_clamped = True
            charge = max(0.0, self._max_cost - self._cost_used)
        self._cost_used += charge
        return charge

    def reset(self) -> None:
        """Restore a fresh budget (new work, not a retry of the same work)."""
        self._attempts_used = 0
        self._cost_used = 0.0
        self._cost_clamped = False

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ResourceBudget(label={self._label!r}, snapshot={self.snapshot()!r})"
