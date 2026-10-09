"""Budget reservations, hard ceilings, and reconciliation.

A run that is allowed to decide how much budget it has left has no budget. This
module owns three things the model cannot reach:

* **The ceilings.** They come from a :class:`~alpha.avo.contracts.BudgetProfile`
  resolved by the server from an identifier. A caller may not pass a number, and
  a number that appears anywhere in a proposed action is ignored.
* **The reservation/reconciliation split.** A reservation is taken *before* an
  action executes and reconciled against *measured* usage afterwards. Without
  the split, a run that is killed mid-action keeps its reservation and slowly
  starves; a run that overruns instead quietly consumes budget it never held.
* **No reset on retry.** ``release`` returns an unspent reservation;
  ``reconcile`` records what was actually spent. There is no ``reset`` and no
  ``grant_more``, so a retry cannot be turned into a fresh allowance.

Exhaustion wording is deliberately the same shape ``alpha.rsi.budgets`` uses,
because a budget refusal that reads one way in one subsystem and another way in
the other is a refusal nobody can correlate across a log.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .contracts import BudgetProfile

__all__ = [
    "BUDGET_KINDS",
    "BudgetExceeded",
    "BudgetKind",
    "BudgetLedger",
    "exhaustion_reason",
    "monotonic_now",
    "reservations_for_action",
]

logger = logging.getLogger("alpha.avo.budgets")


class BudgetKind(StrEnum):
    """The dimensions a run is bounded on. Each maps to one profile field."""

    EXPERIMENT = "experiment"
    ACTION = "action"
    ACTION_PER_EXPERIMENT = "action_per_experiment"
    BRANCH = "branch"
    WALL_TIME_S = "wall_time_s"
    COST_USD = "cost_usd"
    PARALLEL_WORKER = "parallel_worker"
    ACTION_RETRY = "action_retry"
    FAILURE_SIGNATURE = "failure_signature"


#: The closed vocabulary. An unknown kind is refused rather than accepted
#: without a ceiling, because an unbounded dimension is the failure mode.
BUDGET_KINDS: frozenset[BudgetKind] = frozenset(BudgetKind)


def monotonic_now() -> float:
    """Injected clock seam. Tests stub this instead of sleeping."""
    return time.monotonic()


def exhaustion_reason(kind: str, actual: float, limit: float) -> str:
    """``budget_exhausted: <which> <actual>/<limit>`` with the true figures."""
    return f"budget_exhausted: {kind} {actual}/{limit}"


class BudgetExceeded(RuntimeError):
    """A reservation or charge would push usage past a hard ceiling."""

    def __init__(self, kind: str, actual: float, limit: float) -> None:
        self.kind = kind
        self.actual = actual
        self.limit = limit
        super().__init__(f"{kind} {actual} exceeds {limit}")

    @property
    def reason(self) -> str:
        return exhaustion_reason(self.kind, self.actual, self.limit)


_CEILING_FIELD: dict[BudgetKind, str] = {
    BudgetKind.EXPERIMENT: "max_experiments",
    BudgetKind.ACTION: "max_actions",
    BudgetKind.ACTION_PER_EXPERIMENT: "max_actions_per_experiment",
    BudgetKind.BRANCH: "max_branches",
    BudgetKind.WALL_TIME_S: "max_wall_time_seconds",
    BudgetKind.COST_USD: "max_cost_usd",
    BudgetKind.PARALLEL_WORKER: "max_parallel_workers",
    BudgetKind.ACTION_RETRY: "max_retries_per_action",
    BudgetKind.FAILURE_SIGNATURE: "max_same_failure_signature",
}


def ceiling_for(profile: BudgetProfile, kind: BudgetKind) -> float | None:
    """The ceiling for one kind, or ``None`` when it is not enforceable.

    ``None`` is the honest answer for a cost ceiling on a provider whose usage
    the run cannot measure. A fabricated ``inf`` would let a run spend freely
    while reporting a limit.
    """
    value = getattr(profile, _CEILING_FIELD[kind])
    if kind is BudgetKind.COST_USD and value is None:
        return None
    if kind is BudgetKind.COST_USD and not profile.cost_is_enforceable:
        return None
    return float(value)


def reservations_for_action(kind: BudgetKind, amount: float = 1.0) -> dict[BudgetKind, float]:
    """The reservation set one action takes.

    Every action draws on the run-wide action budget and on its own
    experiment's budget, and every *retry* draws on the retry budget. That is
    the whole point of separating them: a run may be well inside its action
    ceiling while one experiment is eating the entire per-experiment allowance.
    """
    reservations: dict[BudgetKind, float] = {
        BudgetKind.ACTION: amount,
        BudgetKind.ACTION_PER_EXPERIMENT: amount,
    }
    if amount != 1.0:
        # A non-unit amount is a retry, and retries are bounded separately so
        # that "try again" cannot be repeated until the action ceiling alone
        # stops it.
        reservations[BudgetKind.ACTION_RETRY] = amount
    return reservations


@dataclass
class _Dimension:
    reserved: float = 0.0
    used: float = 0.0

    @property
    def committed(self) -> float:
        return self.reserved + self.used


@dataclass
class BudgetLedger:
    """Per-run budget accounting. Reservation, charge, and reconciliation.

    Measurements only: nothing here estimates. A dimension that cannot be
    measured (cost on an unmeasured provider) reports ``None`` for its ceiling
    and is tracked as unbounded, which is disclosed in :meth:`snapshot` rather
    than hidden behind a number.
    """

    profile: BudgetProfile
    started_at: float = field(default_factory=monotonic_now)
    _dimensions: dict[str, _Dimension] = field(default_factory=dict)
    _per_experiment: dict[tuple[str, str], _Dimension] = field(default_factory=dict)
    _failure_signatures: dict[str, int] = field(default_factory=dict)
    _released: dict[str, float] = field(default_factory=dict)
    _refusals: list[dict[str, Any]] = field(default_factory=list)

    # -- accounting --------------------------------------------------------
    def ceiling(self, kind: BudgetKind) -> float | None:
        return ceiling_for(self.profile, kind)

    def dimension(self, kind: BudgetKind, *, experiment_id: str | None = None) -> _Dimension:
        if kind not in BUDGET_KINDS:
            raise ValueError(f"unknown budget kind {kind!r}")
        if kind is BudgetKind.ACTION_PER_EXPERIMENT and experiment_id is not None:
            key = (experiment_id, kind.value)
            dim = self._per_experiment.get(key)
            if dim is None:
                dim = _Dimension()
                self._per_experiment[key] = dim
            return dim
        dim = self._dimensions.get(kind.value)
        if dim is None:
            dim = _Dimension()
            self._dimensions[kind.value] = dim
        return dim

    def reserve(
        self,
        kind: BudgetKind,
        amount: float = 1.0,
        *,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        """Reserve ``amount``. Raises :class:`BudgetExceeded` when it cannot."""
        if kind not in BUDGET_KINDS:
            raise ValueError(f"unknown budget kind {kind!r}")
        if amount <= 0:
            raise ValueError(f"reservation amount must be positive, got {amount}")
        ceiling = self.ceiling(kind)
        dim = self.dimension(kind, experiment_id=experiment_id)
        prospective = dim.committed + amount
        if ceiling is not None and prospective > ceiling + 1e-12:
            self._refusals.append(
                {
                    "kind": kind.value,
                    "experiment_id": experiment_id,
                    "actual": prospective,
                    "limit": ceiling,
                    "reason": exhaustion_reason(kind.value, prospective, ceiling),
                }
            )
            raise BudgetExceeded(str(kind.value), prospective, ceiling)
        dim.reserved += amount
        return {
            "kind": kind.value,
            "amount": amount,
            "experiment_id": experiment_id,
            "ceiling": ceiling,
            "committed_after": dim.committed,
        }

    def charge(self, kind: BudgetKind, amount: float, *, experiment_id: str | None = None) -> None:
        """Record *measured* usage. Not bounded: it is history, not a request."""
        if amount < 0:
            raise ValueError(f"charge amount must be non-negative, got {amount}")
        if kind not in BUDGET_KINDS:
            raise ValueError(f"unknown budget kind {kind!r}")
        self.dimension(kind, experiment_id=experiment_id).used += amount

    def release(
        self,
        kind: BudgetKind,
        amount: float,
        *,
        experiment_id: str | None = None,
    ) -> None:
        """Cancel part of a reservation. Returns unspent budget; never adds usage."""
        if amount < 0:
            raise ValueError(f"release amount must be non-negative, got {amount}")
        dim = self.dimension(kind, experiment_id=experiment_id)
        dim.reserved = max(0.0, dim.reserved - amount)
        self._released[kind.value] = self._released.get(kind.value, 0.0) + amount

    def reconcile(
        self,
        kind: BudgetKind,
        *,
        reserved: float,
        measured: float,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        """Turn a reservation into measured usage, releasing the remainder.

        The order is load-bearing: the reservation is released first so a
        shortfall is genuinely returned, then the measurement is charged. A
        reconcile that charged first and released second would double-count an
        overrun and then hand back budget that no longer exists.
        """
        if reserved < 0 or measured < 0:
            raise ValueError("reserved and measured must be non-negative")
        self.release(kind, reserved, experiment_id=experiment_id)
        self.charge(kind, measured, experiment_id=experiment_id)
        return {
            "kind": kind.value,
            "experiment_id": experiment_id,
            "reserved": reserved,
            "measured": measured,
            "variance": round(measured - reserved, 9),
        }

    def record_failure_signature(self, signature: str) -> int:
        """Count one failure signature. Returns the new count for that signature."""
        if not signature:
            raise ValueError("a failure signature must be non-empty to be counted")
        self._failure_signatures[signature] = self._failure_signatures.get(signature, 0) + 1
        return self._failure_signatures[signature]

    def signature_exhausted(self, signature: str) -> bool:
        ceiling = self.ceiling(BudgetKind.FAILURE_SIGNATURE)
        if ceiling is None:
            return False
        return self._failure_signatures.get(signature, 0) >= ceiling

    # -- state -------------------------------------------------------------
    @property
    def exhausted_reason(self) -> str | None:
        """The first ceiling this run has reached, in ``kind actual/limit`` form."""
        for kind in BudgetKind:
            ceiling = self.ceiling(kind)
            if ceiling is None:
                continue
            used = self.dimension(kind).committed
            if used >= ceiling - 1e-12:
                return exhaustion_reason(kind.value, used, ceiling)
        return None

    def is_exhausted(self, kind: BudgetKind | None = None) -> bool:
        if kind is not None:
            ceiling = self.ceiling(kind)
            if ceiling is None:
                return False
            return self.dimension(kind).committed >= ceiling - 1e-12
        return self.exhausted_reason is not None

    def elapsed_seconds(self) -> float:
        return monotonic_now() - self.started_at

    def remaining(self, kind: BudgetKind) -> float | None:
        ceiling = self.ceiling(kind)
        if ceiling is None:
            return None
        return max(0.0, ceiling - self.dimension(kind).committed)

    def snapshot(self) -> dict[str, Any]:
        """The whole account, including what is *not* measurable."""
        dimensions: dict[str, Any] = {}
        for name in sorted({*(d.value for d in BudgetKind)}):
            kind = BudgetKind(name)
            ceiling = self.ceiling(kind)
            dim = self.dimension(kind)
            dimensions[name] = {
                "ceiling": ceiling,
                "reserved": round(dim.reserved, 9),
                "used": round(dim.used, 9),
                "remaining": None if ceiling is None else round(max(0.0, ceiling - dim.committed), 9),
                "ceiling_enforceable": ceiling is not None,
            }
        return {
            "profile_id": self.profile.profile_id,
            "elapsed_seconds": round(self.elapsed_seconds(), 6),
            "dimensions": dimensions,
            "per_experiment": {
                f"{experiment_id}:{kind}": {
                    "reserved": round(dim.reserved, 9),
                    "used": round(dim.used, 9),
                }
                for (experiment_id, kind), dim in sorted(self._per_experiment.items())
            },
            "failure_signatures": dict(sorted(self._failure_signatures.items())),
            "released": {key: round(value, 9) for key, value in sorted(self._released.items())},
            "refusals": list(self._refusals),
            "exhausted_reason": self.exhausted_reason,
            "unbounded_dimensions": sorted(name for name in (k.value for k in BudgetKind) if self.ceiling(BudgetKind(name)) is None),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()
