"""The context-exhaustion ladder: what happens when a window is about to fill.

The failure this module exists to prevent is the silent one. An agent whose
request overflows its window has exactly two bad options available to a runtime
that never planned for it: crash with a provider error the user cannot act on,
or quietly drop history and hope. The industry guidance is blunt about the
alternative — *overflow should trigger a defined handler: compress, truncate,
or summarize, not a silent API error* — and that handler has to be an ordered,
declared ladder, because the order is the whole decision.

## The ladder

Ordered from least destructive to most, because a step that discards less is
strictly better when both would restore headroom:

| Step | What it does | What it costs |
|---|---|---|
| ``compact`` | summarize old turns into ``summary_text``, keep the recent window | one model call; a summary is lossy |
| ``evict_tool_outputs`` | drop the largest non-authoritative messages | fidelity of evicted turns only |
| ``escalate_window`` | re-route to a model with a larger declared window | money, and a model the user did not pick |
| ``park`` | hold the run in a durable ``WAITING`` session state | the task waits for an operator |
| ``fail_closed`` | terminalize with a named reason | the task did not finish |

Two properties are load-bearing and are pinned by tests:

* **Order is never reordered by availability.** An unavailable step stays in the
  plan carrying ``available=False`` and its reason, so a caller sees *which*
  rung was skipped and why. Dropping it would let a plan of three available
  steps read as "the only three options", which is how a parked run gets
  mistaken for an exhausted one.
* **The ladder never invents a step.** Every rung is gated on a capability the
  host actually reported; ``plan_context_recovery`` is pure and performs no
  I/O, so it cannot claim a compaction that will not run.

This module decides **what to do**. It does not do it. Compaction belongs to
:mod:`alpha.runtime.context_compaction` and
``alpha.agents.middlewares.summarization_middleware``; parking belongs to
``SafeRunRecoveryService`` through the normal stop-reason path, and this module
is not a second continuation authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from alpha.runtime.context_window import ContextPressure, ContextWindowSpec, is_pre_emptive

#: One rung of the ladder. ``fail_closed`` is the terminal rung and is the only
#: one with no "cost beyond itself" — everything before it is a recovery attempt.
ContextExhaustionAction = Literal[
    "compact",
    "evict_tool_outputs",
    "escalate_window",
    "park",
    "fail_closed",
]

#: The ladder in order. This is the single source of the ordering; a caller
#: that needs to render the rungs renders this tuple.
CONTEXT_EXHAUSTION_LADDER: tuple[ContextExhaustionAction, ...] = (
    "compact",
    "evict_tool_outputs",
    "escalate_window",
    "park",
    "fail_closed",
)

#: Bands for which the full ladder is declared. Below ``over`` the plan is a
#: pre-emptive single step (compact), because acting earlier is cheaper than
#: acting correctly.
_RECOVERY_BANDS: frozenset[str] = frozenset({"critical", "over"})

#: Stable machine-readable reasons attached to each rung.
REASON_STEP_AVAILABLE = "step_available"
REASON_STEP_UNAVAILABLE = "step_capability_not_reported"
REASON_STEP_PROACTIVE = "step_pre_emptive"
REASON_NO_ESCALATION_TARGET = "no_model_with_larger_window"
REASON_NO_COMPACTION = "compaction_not_enabled"
REASON_NO_EVICTION = "eviction_not_enabled"
REASON_NO_PARK = "parking_not_available"
REASON_PLAN_EMPTY = "no_action_required"

#: Stop reasons a run may carry when the ladder terminates. ``park`` and
#: ``fail_closed`` are the two that reach durable run state, so they are named
#: here rather than spelled at each call site.
CONTEXT_WINDOW_STOP_REASONS: dict[str, str] = {
    "park": "context_window_parked",
    "fail_closed": "context_window_exhausted",
}


@dataclass(frozen=True)
class ContextExhaustionStep:
    """One rung, and whether the host can actually run it.

    ``available`` is the only thing a caller may branch on. ``reason`` explains
    the value — never the reverse, because a caller that branches on the prose
    is a caller that stops working when the sentence is reworded.
    """

    action: ContextExhaustionAction
    available: bool
    reason: str

    @property
    def stop_reason(self) -> str | None:
        """The durable stop reason this rung persists, when it persists one."""
        return CONTEXT_WINDOW_STOP_REASONS.get(self.action)


@dataclass(frozen=True)
class ContextExhaustionPlan:
    """The ordered rungs for one pressure reading.

    ``empty`` is a state, not a failure: a ``nominal`` reading declares no rungs
    at all, and a caller that treats that as "no plan available" would go looking
    for a ladder nobody needs.
    """

    band: str
    steps: tuple[ContextExhaustionStep, ...]
    reason: str

    @property
    def empty(self) -> bool:
        return not self.steps

    @property
    def first_available(self) -> ContextExhaustionStep | None:
        """The first rung the host said it can run, or ``None``.

        Never ``None`` for a recovery band, because ``fail_closed`` is the one
        rung that is always available. That makes this a *total* function — a
        caller can always act — but it also means a plan whose first available
        rung is the terminal one is a plan where every recovery rung was
        refused, which is why ``unavailable_reasons`` exists and why a caller
        acting on ``fail_closed`` should log it.
        """
        for step in self.steps:
            if step.available:
                return step
        return None

    @property
    def unavailable_reasons(self) -> tuple[str, ...]:
        """Why each skipped rung was skipped, in ladder order."""
        return tuple(step.reason for step in self.steps if not step.available)


def _step(
    action: ContextExhaustionAction,
    available: bool,
    reason_when_unavailable: str,
) -> ContextExhaustionStep:
    return ContextExhaustionStep(
        action=action,
        available=available,
        reason=REASON_STEP_AVAILABLE if available else reason_when_unavailable,
    )


def plan_context_recovery(
    pressure: ContextPressure,
    *,
    compaction_available: bool = False,
    eviction_available: bool = False,
    escalation_available: bool = False,
    park_available: bool = False,
) -> ContextExhaustionPlan:
    """Declare the ordered rungs for one pressure reading.

    Pure and total: every input combination produces a plan, and no branch
    raises. A ``nominal`` or ``unknown`` reading declares no rungs — acting on
    a window nobody measured would spend a compaction call on a thread that is
    already comfortable, and reporting ``over`` for an undeclared window would
    park healthy work on a lie.
    """
    if not is_pre_emptive(pressure.band) or pressure.band not in _RECOVERY_BANDS:
        return ContextExhaustionPlan(band=pressure.band, steps=(), reason=REASON_PLAN_EMPTY)

    # ``over`` needs the whole ladder; ``critical`` is pre-empted by the one
    # step that costs the least, so a thread that is merely close is compacted
    # rather than escalated to a more expensive model.
    if pressure.band == "critical":
        steps = (
            _step("compact", compaction_available, REASON_NO_COMPACTION),
            _step("fail_closed", True, REASON_STEP_UNAVAILABLE),
        )
        reason = REASON_STEP_PROACTIVE
    else:
        steps = (
            _step("compact", compaction_available, REASON_NO_COMPACTION),
            _step("evict_tool_outputs", eviction_available, REASON_NO_EVICTION),
            _step("escalate_window", escalation_available, REASON_NO_ESCALATION_TARGET),
            _step("park", park_available, REASON_NO_PARK),
            _step("fail_closed", True, REASON_STEP_UNAVAILABLE),
        )
        reason = REASON_STEP_AVAILABLE

    return ContextExhaustionPlan(band=pressure.band, steps=steps, reason=reason)


def escalation_candidate(
    *,
    declared_windows: dict[str, int],
    current_window: int | None,
) -> str | None:
    """Name a model whose declared window is strictly larger, or ``None``.

    Ties are broken by name so two deployments with the same catalog resolve
    the same way. A candidate whose window is merely *equal* is refused: routing
    to a same-sized window is a model switch with no headroom bought, which is
    a cost the user pays for nothing.
    """
    if current_window is None:
        # Without the current size, "larger" is unanswerable, so refusing is the
        # honest answer rather than picking the largest window in the catalog.
        return None
    larger = sorted(name for name, window in declared_windows.items() if window > current_window)
    return larger[0] if larger else None


#: Specs are accepted so a caller can validate the shape of what it is about to
#: act on without this module learning how one is built.
__all__ = [
    "CONTEXT_EXHAUSTION_LADDER",
    "CONTEXT_WINDOW_STOP_REASONS",
    "REASON_NO_COMPACTION",
    "REASON_NO_ESCALATION_TARGET",
    "REASON_NO_EVICTION",
    "REASON_NO_PARK",
    "REASON_PLAN_EMPTY",
    "REASON_STEP_AVAILABLE",
    "REASON_STEP_PROACTIVE",
    "REASON_STEP_UNAVAILABLE",
    "ContextExhaustionAction",
    "ContextExhaustionPlan",
    "ContextExhaustionStep",
    "ContextPressure",
    "ContextWindowSpec",
    "escalation_candidate",
    "plan_context_recovery",
]
