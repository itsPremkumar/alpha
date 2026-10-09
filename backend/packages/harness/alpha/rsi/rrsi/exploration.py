"""Regularizer **P3** — stall-triggered structured exploration.

RRSI's third proposal-side regularizer refuses to let a search keep proposing
inside the one component it has already exhausted. When the best score has
improved by less than the noise band ``δ`` over the last ``w`` rounds the
round is *stalled*, and ``m_draft`` proposal slots are reserved for components
the search has never touched — preferring the structural subset ``K_struct``,
because a re-prompted trajectory inside one exhausted component is exactly the
"same trajectory wearing new words" that unregularized RSI degenerates into.

Binding rules:

* **A stall is computed from scores the caller actually has.** With fewer than
  ``w + 1`` rounds of history there is nothing to compare across the window, so
  the signal reports ``stalled=False`` **with that reason** rather than
  declaring a stall it cannot support — and equally not silently "fine".
* **An unreadable ledger means the plan cannot be built, not that nothing is
  untried.** When :class:`~alpha.rsi.rrsi.history.RrsiHistory` reports
  ``readable=False`` the plan carries ``status="unavailable"`` and reserves no
  named components. Naming any would require knowing what has been attempted,
  which is precisely what could not be read.
* **Reserved slots are a reservation, not a claim of diversity.** The plan
  reports how many slots are held and which components they are for; it does
  not estimate how exploratory the resulting round will be.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from alpha.rsi.rrsi.components import COMPONENTS, STRUCTURAL_COMPONENTS
from alpha.rsi.rrsi.config import RrsiParams
from alpha.rsi.rrsi.history import RrsiHistory

__all__ = ["ExplorationPlan", "StallSignal", "detect_stall", "plan_exploration"]


@dataclass(frozen=True)
class StallSignal:
    """Whether the search has stalled over the last ``stall_window`` rounds.

    ``delta`` is the *absolute* change in the best score from ``w`` rounds ago
    to now; ``None`` when fewer than ``w + 1`` observations exist. ``threshold``
    is the relative noise band ``δ`` the improvement is compared against.
    ``observed`` is the number of rounds actually available.
    """

    stalled: bool
    reason: str
    delta: float | None
    threshold: float
    window: int
    observed: int

    def to_dict(self) -> dict[str, object]:
        return {"stalled": self.stalled, "reason": self.reason, "delta": self.delta, "threshold": self.threshold, "window": self.window, "observed": self.observed}


def detect_stall(scores: Sequence[float], params: RrsiParams | None = None) -> StallSignal:
    """Compare the current best score against the one ``stall_window`` rounds ago.

    ``scores`` must be the best score *per round* in chronological order. It is
    refused as a bare string (which would otherwise iterate as characters and
    quietly produce a nonsense stall verdict).
    """
    params = (params or RrsiParams()).validate()
    if isinstance(scores, (str, bytes)):
        raise ValueError("scores must be a sequence of round scores, not a string.")
    window = params.stall_window
    observed = len(scores)
    if observed < window + 1:
        return StallSignal(
            stalled=False,
            reason=f"insufficient history to detect a stall: {observed} round score(s) available, {window + 1} required for a {window}-round window — no stall verdict is supported.",
            delta=None,
            threshold=params.noise_delta,
            window=window,
            observed=observed,
        )
    reference = float(scores[-(window + 1)])
    current = float(scores[-1])
    delta = current - reference
    stalled = delta <= params.noise_delta
    if stalled:
        reason = f"best score moved {delta:+.6g} over the last {window} round(s), at or below the noise band δ = {params.noise_delta}; stalled (σ_t = 1)."
    else:
        reason = f"best score moved {delta:+.6g} over the last {window} round(s), above the noise band δ = {params.noise_delta}; not stalled (σ_t = 0)."
    return StallSignal(stalled=stalled, reason=reason, delta=delta, threshold=params.noise_delta, window=window, observed=observed)


@dataclass(frozen=True)
class ExplorationPlan:
    """How this round reserves capacity for components the search has not tried.

    ``status`` is one of:

    ``"applied"``
        the plan is actionable — ``reserved_components`` names where the
        reserved slots point (may be empty if ``K`` is fully exercised).
    ``"not_stalled"``
        exploration is not due this round; ``slots`` is ``0``.
    ``"unavailable"``
        the ledger could not be read, so no component may be named.
        ``slots`` still reports how many slots the stall signal reserves, and
        ``reason`` carries the real read failure.
    """

    status: str
    stalled: bool
    slots: int
    reserved_components: tuple[str, ...]
    untried_components: tuple[str, ...]
    structural_priority: tuple[str, ...]
    reason: str

    @property
    def applied(self) -> bool:
        """Whether this plan actually redirects proposal capacity."""
        return self.status == "applied" and self.slots > 0 and bool(self.reserved_components)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "stalled": self.stalled,
            "slots": self.slots,
            "reserved_components": list(self.reserved_components),
            "untried_components": list(self.untried_components),
            "structural_priority": list(self.structural_priority),
            "applied": self.applied,
            "reason": self.reason,
        }


def plan_exploration(stall: StallSignal, history: RrsiHistory, params: RrsiParams | None = None) -> ExplorationPlan:
    """Turn a stall signal plus the ledger into reserved exploration slots.

    ``U_t = K \\ T_t`` is set-defined by the paper; the *order* the reserved
    slots fill from is a choice, and this implementation puts ``K_struct``
    first. The reason is mechanical rather than aesthetic: ``ν_t`` — the only
    novelty credit the within-band rule will grant — counts structural
    components, so a reserved slot spent on a non-structural component cannot
    buy admission for the candidate it produces. The ordering is therefore
    disclosed through :attr:`ExplorationPlan.structural_priority` rather than
    being baked in invisibly.
    """
    params = (params or RrsiParams()).validate()
    if not isinstance(stall, StallSignal):
        raise ValueError("plan_exploration() takes a StallSignal.")
    if not stall.stalled:
        return ExplorationPlan(
            status="not_stalled",
            stalled=False,
            slots=0,
            reserved_components=(),
            untried_components=(),
            structural_priority=(),
            reason=f"not stalled: {stall.reason}",
        )
    exercised = history.exercised_components()
    if exercised is None:
        return ExplorationPlan(
            status="unavailable",
            stalled=True,
            slots=params.exploratory_slots,
            reserved_components=(),
            untried_components=(),
            structural_priority=(),
            reason=f"stalled ({stall.reason}) but the credit-assignment ledger could not be read: {history.load_error}. No component is named — 'untried' is exactly the fact that is unavailable.",
        )
    untried_set = set(_all_components()) - set(exercised)
    ordered = tuple(component for component in _ordered_components() if component in untried_set)
    reserved = ordered[: params.exploratory_slots]
    if not reserved:
        reason = f"stalled ({stall.reason}) and every component of K has been exercised; {params.exploratory_slots} exploratory slot(s) have nowhere to point."
    else:
        reason = f"stalled ({stall.reason}); {len(reserved)} of {params.exploratory_slots} reserved slot(s) point at untried component(s): {', '.join(reserved)}"
    return ExplorationPlan(
        status="applied",
        stalled=True,
        slots=params.exploratory_slots,
        reserved_components=reserved,
        untried_components=ordered,
        structural_priority=tuple(component for component in _ordered_components() if component in STRUCTURAL_COMPONENTS),
        reason=reason,
    )


def _all_components() -> tuple[str, ...]:
    return COMPONENTS


def _ordered_components() -> tuple[str, ...]:
    """``K`` with ``K_struct`` first — the exploration reservation order."""
    structural = [name for name in COMPONENTS if name in STRUCTURAL_COMPONENTS]
    rest = [name for name in COMPONENTS if name not in STRUCTURAL_COMPONENTS]
    return tuple(structural + rest)
