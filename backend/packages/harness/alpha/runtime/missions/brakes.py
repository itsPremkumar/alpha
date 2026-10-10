"""The fail-closed loop brake: continue, done, or park — never loop forever.

Why this exists
--------------
The consistent lesson across the open-source Codex loop controllers
(``autonomous-loop``'s Stop hook, ``codex-loop``'s three limiters, ``AxiomGate``'s
Build Receipt, and Codex issue #37937) is that an autonomous loop must **not trust
the agent's own claim of completion**, and must **not run forever** when it is
stuck: a repeated block with no new information should *fail closed once* and
return control to a human, rather than burn another turn — and another — until a
quota dies.

:func:`decide_mission` is that decision, and it is deliberately **model-free**.
Reading only durable mission state (:class:`~alpha.runtime.missions.manager.MissionStack`),
it returns exactly one of three actions. A deterministic substrate is what makes
the "why did it stop" question answerable after the fact, and what stops the agent
from redefining "done" to whatever it last produced.

The three outcomes
------------------
``DONE`` only when the plan is fully ``VERIFIED`` on measured evidence — never on a
summary. ``PARK`` on any of three fail-closed conditions: an explicit ``blocked``,
the same attempt *signature* repeating past the threshold (no new information), or
``no_progress_cycles`` crossing the threshold (activity without progress).
``CONTINUE`` otherwise, naming the current milestone. ``PARK`` is not a failure; it
is the loop handing control back because continuing is provably not converging.

Honesty rules encoded here
--------------------------
* ``measured`` evidence, not prose, drives ``DONE`` (the milestone plan already
  refuses to verify without a record).
* A park always carries the reason and the counters that produced it, so an
  operator sees *why* it stopped, not just that it did.
* The brake cannot widen itself: it has no path to "keep going anyway", only the
  three terminal actions. Progress resets the no-progress counter; an activity
  pulse with no state change does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from alpha.runtime.missions.manager import MissionStack

__all__ = [
    "DEFAULT_NO_PROGRESS_CYCLES",
    "DEFAULT_REPEATED_FAILURE",
    "LoopDecision",
    "MissionAction",
    "decide_mission",
]

#: Consecutive no-progress cycles before the mission parks. Generous enough that
#: a slow-but-real milestone is not cut off; small enough that a stuck loop stops
#: long before an unattended run has burned a day's budget on it.
DEFAULT_NO_PROGRESS_CYCLES: Final[int] = 6

#: Times the same attempt signature may repeat before the mission parks on "no
#: new information" — the same-failure-signature escalation the community loop
#: controllers use to hard-stop.
DEFAULT_REPEATED_FAILURE: Final[int] = 3


class MissionAction(StrEnum):
    """What the loop should do with the mission right now."""

    CONTINUE = "continue"
    DONE = "done"
    PARK = "park"


@dataclass(frozen=True, slots=True)
class LoopDecision:
    """A model-free verdict on the mission's next step."""

    action: MissionAction
    reason: str
    cycle_count: int = 0
    no_progress_cycles: int = 0
    repeated_signature: str | None = None
    current_milestone: str | None = None

    @property
    def should_park(self) -> bool:
        return self.action is MissionAction.PARK

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "cycle_count": self.cycle_count,
            "no_progress_cycles": self.no_progress_cycles,
            "repeated_signature": self.repeated_signature,
            "current_milestone": self.current_milestone,
        }


def _most_repeated(attempts: tuple[str, ...]) -> tuple[str | None, int]:
    counts: dict[str, int] = {}
    for signature in attempts:
        counts[signature] = counts.get(signature, 0) + 1
    if not counts:
        return None, 0
    signature, count = max(counts.items(), key=lambda kv: kv[1])
    return signature, count


def decide_mission(
    stack: MissionStack,
    *,
    no_progress_threshold: int = DEFAULT_NO_PROGRESS_CYCLES,
    repeated_failure_threshold: int = DEFAULT_REPEATED_FAILURE,
) -> LoopDecision:
    """Return the fail-closed next action for *stack*, from durable state only.

    Order is the contract: an explicit block and "done" are absolute; a repeated
    signature (no new information) outranks a mere no-progress count; everything
    else continues on the current milestone.
    """
    plan = stack.plan
    current = plan.current.id if plan is not None and plan.current is not None else None

    if stack.blocked.strip():
        return LoopDecision(MissionAction.PARK, f"blocked: {stack.blocked.strip()}", stack.cycle_count, stack.no_progress_cycles, current_milestone=current)

    if plan is not None and plan.total > 0 and plan.complete:
        return LoopDecision(MissionAction.DONE, "every milestone is verified on measured evidence", stack.cycle_count, stack.no_progress_cycles, current_milestone=current)

    signature, count = _most_repeated(stack.attempts)
    if signature is not None and count >= max(2, repeated_failure_threshold):
        return LoopDecision(
            MissionAction.PARK, f"the same attempt repeated {count} times with no new information; stopping to avoid a stuck loop", stack.cycle_count, stack.no_progress_cycles, repeated_signature=signature, current_milestone=current
        )

    if stack.no_progress_cycles >= max(1, no_progress_threshold):
        return LoopDecision(MissionAction.PARK, f"no measured progress across {stack.no_progress_cycles} cycles; returning control", stack.cycle_count, stack.no_progress_cycles, current_milestone=current)

    return LoopDecision(MissionAction.CONTINUE, f"proceeding on milestone {current!r}" if current else "no plan yet; a mission needs an objective and milestones", stack.cycle_count, stack.no_progress_cycles, current_milestone=current)
