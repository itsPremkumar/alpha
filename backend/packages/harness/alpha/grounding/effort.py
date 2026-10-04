"""Deciding how hard to work, and when to stop trying.

## The loop that goes wrong

The instinct on repeated failure is "go research the solution and try again". As a
bare rule that is a trap, and the reason is measurable: task accuracy against
chain-of-thought length is **inverted-U**. Extended reasoning is reliably
associated with *abandoning a previously correct answer*, and marginal returns
diminish sharply at higher budgets. A self-directed research loop with no brake
does not converge — it wanders off a correct solution it already had.

Two more findings shape the ladder:

* Models are bad at knowing when they do not know (abstention rates on knowledge
  benchmarks range from ~1% to ~52% with accuracy barely moving), so **the loop's
  trigger cannot be the model's own report of failure**. It is driven by observed
  step outcomes.
* A reflexion-style loop works. It needs a bounded ladder, an explicit floor, and
  an escalation target — which is what this module is.

## Complexity tiers gate machinery, they do not describe it

Tier comparison uses ``rank``, never the enum member. The tiers are string enums,
so ``tier >= COMPLEX`` compares strings and silently inverts on the most complex
tasks — the exact class of bug this repository has already hit once in the swarm
strategy resolver. The rule is enforced by making :attr:`EffortTier.rank` the only
supported comparison.

## The brake, stated honestly

:meth:`EffortController.should_continue` returns ``False`` in three distinct
situations, and they are different diagnoses with different fixes:

* ``budget_exhausted`` — the allowance is spent. Give more budget.
* ``no_progress`` — the same approach is failing identically. Change approach.
* ``overthinking`` — a correct answer was reached and then abandoned. Stop; the
  current answer is probably the better one.

Reporting these as one boolean would make all three look like "try harder", which
is the failure mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.grounding.models import Escalation

__all__ = [
    "EffortController",
    "EffortTier",
    "EscalationStep",
    "StopReason",
    "WorkOutcome",
]


class EffortTier(StrEnum):
    """How much machinery a task earns.

    ``rank`` is the comparison key. Never compare the members directly.
    """

    DIRECT = "direct"
    STANDARD = "standard"
    DEEP = "deep"
    FULL = "full"

    @property
    def rank(self) -> int:
        return {"direct": 0, "standard": 1, "deep": 2, "full": 3}[self.value]

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, EffortTier):
            return self.rank < other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, EffortTier):
            return self.rank <= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, EffortTier):
            return self.rank > other.rank
        return NotImplemented

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, EffortTier):
            return self.rank >= other.rank
        return NotImplemented


class EscalationStep(StrEnum):
    """One rung, in order.

    The ordering is the design. Same-approach retry is allowed once and no more,
    because a second identical failure is evidence rather than bad luck, and
    research-before-retry comes before research-after-failure so the loop spends
    its budget on a genuinely new approach rather than a re-read.
    """

    #: Repeat the current step verbatim. Allowed once.
    RETRY_SAME = "retry_same"
    #: Gather context, then retry once.
    RESEARCH = "research"
    #: Same goal, different method.
    CHANGE_APPROACH = "change_approach"
    #: Different tool or subsystem.
    CHANGE_TOOL = "change_tool"
    #: A specialist agent owns it.
    DELEGATE = "delegate"
    #: Hand back to a person.
    ESCALATE_HUMAN = "escalate_human"
    #: Give up and report honestly.
    ABSTAIN = "abstain"


#: The ladder, in order. This exact sequence is asserted by a test; reordering it
#: is a behaviour change and not a refactor.
LADDER: tuple[EscalationStep, ...] = (
    EscalationStep.RETRY_SAME,
    EscalationStep.RESEARCH,
    EscalationStep.CHANGE_APPROACH,
    EscalationStep.CHANGE_TOOL,
    EscalationStep.DELEGATE,
    EscalationStep.ESCALATE_HUMAN,
)

#: What each rung maps to in the shared action vocabulary, so a caller does not
#: have to translate and can never translate two rungs onto one action.
STEP_ESCALATION: dict[EscalationStep, Escalation] = {
    EscalationStep.RETRY_SAME: Escalation.PROCEED,
    EscalationStep.RESEARCH: Escalation.PROCEED,
    EscalationStep.CHANGE_APPROACH: Escalation.CHANGE_TOOL,
    EscalationStep.CHANGE_TOOL: Escalation.CHANGE_TOOL,
    EscalationStep.DELEGATE: Escalation.DELEGATE,
    EscalationStep.ESCALATE_HUMAN: Escalation.ASK_USER,
    EscalationStep.ABSTAIN: Escalation.ABSTAIN,
}


class StopReason(StrEnum):
    """Why the loop stopped. Distinct values are distinct diagnoses."""

    #: The allowance is spent.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: Repeating the same approach keeps failing identically.
    NO_PROGRESS = "no_progress"
    #: A correct answer was reached and then abandoned.
    OVERTHINKING = "overthinking"
    #: Reached an irreversible rung.
    LADDER_EXHAUSTED = "ladder_exhausted"
    #: Someone asked it to stop.
    CANCELLED = "cancelled"


class WorkOutcome(StrEnum):
    """What one attempt did."""

    SUCCESS = "success"
    #: Failed, and the attempt produced new evidence.
    FAILED_PROGRESS = "failed_progress"
    #: Failed exactly as the last one did.
    FAILED_REPEAT = "failed_repeat"
    #: Failed in a way that matches nothing seen before.
    FAILED_NOVEL = "failed_novel"


@dataclass
class EffortController:
    """Bounded effort with an explicit escalation ladder.

    Holds no model state and reads no clock in the decision path: ``record`` is
    driven by observed outcomes and ``now`` is injected, so a run's escalation
    trace replays identically.
    """

    #: Steps allowed before the brake engages.
    max_steps: int = 8
    #: Consecutive identical failures tolerated before :attr:`StopReason.NO_PROGRESS`.
    max_consecutive_failures: int = 3
    #: How many :attr:`EscalationStep.RETRY_SAME` rungs exist in total.
    max_identical_retries: int = 1
    #: Track whether a correct answer was reached and later abandoned.
    detect_overthinking: bool = True

    steps_used: int = field(default=0, init=False, repr=False)
    consecutive_failures: int = field(default=0, init=False, repr=False)
    identical_retries: int = field(default=0, init=False, repr=False)
    ladder_index: int = field(default=0, init=False, repr=False)
    _last_failure: str | None = field(default=None, init=False, repr=False)
    #: Sticky: a success was reached at *some* point in this trajectory. Never
    #: cleared by a later failure -- the overthinking signal is precisely "a
    #: working answer existed and the run walked away from it", so clearing this
    #: on the first failure destroys the only evidence it ever happened.
    _success_reached: bool = field(default=False, init=False, repr=False)
    trace: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    # -- tier ------------------------------------------------------------

    @staticmethod
    def classify_tier(*, steps: int, files_touched: int = 0, distinct_domains: int = 0, subagent_used: bool = False) -> EffortTier:
        """A cheap, deterministic complexity estimate.

        Deliberately not model-driven. Effort gating is exactly the decision a
        miscalibrated model makes worst, and routing on a stated difficulty
        estimate would put the brake under the thing it exists to restrain.
        """
        weight = steps + (files_touched * 2) + (distinct_domains * 3)
        if subagent_used or weight >= 15:
            return EffortTier.FULL
        if weight >= 8:
            return EffortTier.DEEP
        if weight >= 3:
            return EffortTier.STANDARD
        return EffortTier.DIRECT

    def allows(self, requirement: EffortTier) -> bool:
        """Whether machinery for *requirement* should be enabled.

        Compares ``rank``. Never the member.
        """
        return self.tier.rank >= requirement.rank

    @property
    def tier(self) -> EffortTier:
        if self.steps_used == 0:
            return EffortTier.STANDARD
        if self.steps_used >= self.max_steps:
            return EffortTier.FULL
        return EffortTier.STANDARD

    # -- the loop --------------------------------------------------------

    def record(self, outcome: WorkOutcome, *, signature: str = "", escalate: bool = False) -> None:
        """Record one attempt's outcome.

        ``signature`` identifies *how* it failed, so two different failures are
        not mistaken for one repeated failure. Without it, "failed again" and
        "failed differently" are indistinguishable and the ladder advances when it
        should hold.
        """
        self.steps_used += 1
        self.trace.append({"step": self.steps_used, "outcome": outcome.value, "signature": signature})

        if outcome is WorkOutcome.SUCCESS:
            self.consecutive_failures = 0
            self._last_failure = None
            self._success_reached = True
            return

        if outcome is WorkOutcome.FAILED_REPEAT or (outcome is WorkOutcome.FAILED_PROGRESS and signature and signature == self._last_failure):
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 1
        self._last_failure = signature or self._last_failure

        # A failure recorded while the ladder still sits on RETRY_SAME is a
        # same-step retry: the loop was told to repeat the current step
        # verbatim and it failed again. Count it so the RETRY_SAME brake in
        # ``stop_reason`` can spend the ``max_identical_retries`` allowance
        # and advance. The counter was declared and read by that brake but
        # never incremented, so ``0 > max_identical_retries`` was always
        # false and the operator-tunable ``max_identical_retries`` field had
        # no effect -- a silent configuration failure.
        if self.current_step is EscalationStep.RETRY_SAME:
            self.identical_retries += 1

        if escalate:
            self.advance()

    def advance(self) -> EscalationStep | None:
        """Move up one rung. Returns the new rung, or ``None`` at the top."""
        if self.ladder_index >= len(LADDER) - 1:
            return None
        self.ladder_index += 1
        return self.current_step

    @property
    def current_step(self) -> EscalationStep:
        return LADDER[min(self.ladder_index, len(LADDER) - 1)]

    @property
    def current_escalation(self) -> Escalation:
        return STEP_ESCALATION[self.current_step]

    def should_continue(self) -> bool:
        """The brake. ``False`` means stop and report, do not try harder."""
        return self.stop_reason() is None

    def stop_reason(self) -> StopReason | None:
        """Why to stop, or ``None`` to keep going.

        Order is deliberate: overthinking is checked before budget, because
        "stop, your last answer was better" is the most valuable message this
        function can produce and it should not be reported as a budget problem.
        """
        if self.detect_overthinking and self._success_reached and self.consecutive_failures > 0:
            # A success was recorded and the step after it failed; the current
            # answer is very likely still the better one.
            return StopReason.OVERTHINKING
        if self.steps_used >= self.max_steps:
            return StopReason.BUDGET_EXHAUSTED
        if self.consecutive_failures >= self.max_consecutive_failures:
            return StopReason.NO_PROGRESS
        if self.identical_retries > self.max_identical_retries and self.current_step is EscalationStep.RETRY_SAME:
            self.advance()
        if self.ladder_index >= len(LADDER) - 1 and self.consecutive_failures > 0:
            return StopReason.LADDER_EXHAUSTED
        return None

    def budget_remaining(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    def report(self) -> dict[str, Any]:
        reason = self.stop_reason()
        return {
            "tier": self.tier.value,
            "steps_used": self.steps_used,
            "max_steps": self.max_steps,
            "budget_remaining": self.budget_remaining(),
            "consecutive_failures": self.consecutive_failures,
            "ladder_step": self.current_step.value,
            "escalation": self.current_escalation.value,
            "stop_reason": reason.value if reason else None,
            "should_continue": reason is None,
            "trace": list(self.trace),
        }


def escalation_for_stop(reason: StopReason | None) -> Escalation:
    """Map a stop decision onto the shared action vocabulary.

    A consumer should never have to switch on two enums, so this is the single
    translation point.
    """
    if reason is None:
        return Escalation.PROCEED
    if reason is StopReason.OVERTHINKING:
        # Revert to the recorded success rather than escalate further.
        return Escalation.PROCEED
    if reason is StopReason.BUDGET_EXHAUSTED:
        return Escalation.DELEGATE
    if reason is StopReason.NO_PROGRESS:
        return Escalation.CHANGE_TOOL
    if reason is StopReason.LADDER_EXHAUSTED:
        return Escalation.ASK_USER
    return Escalation.ABSTAIN


def tier_requirements(steps_hint: Sequence[str] | None = None) -> dict[EffortTier, tuple[str, ...]]:
    """Which machinery each tier earns.

    Stated as data so the mapping is testable and so adding a tier cannot quietly
    change what an existing one enables.
    """
    return {
        EffortTier.DIRECT: (),
        EffortTier.STANDARD: ("reuse_probe",),
        EffortTier.DEEP: ("reuse_probe", "claim_ledger", "preflight_research"),
        EffortTier.FULL: ("reuse_probe", "claim_ledger", "preflight_research", "solvability_gate", "independent_verifier"),
    }
