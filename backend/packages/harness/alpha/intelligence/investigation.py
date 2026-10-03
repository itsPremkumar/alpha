"""Phase G — investigation selection: rank what is worth *investigating*.

The gap
-------
The strongest empirical result about self-improving agents (MIT Technology
Review, Aug 2026, reporting a NeurIPS 2026 study) is that they can do all the
**engineering** of open-ended research and are "unambiguously bad at carrying out
the research itself". The missing capability is **judgment**: knowing which
question is worth asking.

Alpha already has strong `critic`, `council`, `deliberation` and `epistemics`.
What it lacks is a mechanism that decides *which problem to attack* rather than
ranking problems it was handed. `alpha.rsi.opportunity.mine` prioritises given
signals; it does not choose what to investigate.

Three rules keep this a measurement rather than a novelty generator
--------------------------------------------------------------------
**1. ``falsifiable_by`` is required and must name a measurement.**
A proposal with no refutation condition is refused at construction. Without
this rule the module's job reduces to generating interesting-sounding questions,
and interesting-sounding questions are the failure mode the NeurIPS study is
describing.

**2. Priority is information gain per unit budget.**
Cost is a :class:`~alpha.intelligence.budget_protocol.BudgetUnit` from Phase D, so
"expensive" means the same thing here as it does in a matched comparison.

**3. The selection is itself gated.**
Choosing what to investigate is *precisely* the judgment agents were measured as
bad at. So a selected investigation must pass the Phase E convergence check
before it is allowed to consume budget. An unverified self-assessment of "this is
worth investigating" is exactly the kind of claim Alpha's other five subsystems
exist to refuse.

Explicitly out of scope
-----------------------
Generating novel research directions from model weights. Alpha should rank
investigations it can **falsify**, not invent ones it cannot.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.intelligence.budget_protocol import BudgetUnit
from alpha.intelligence.evidence_ledger import ConvergedVerdict, ConvergenceStatus

__all__ = [
    "GapKind",
    "InvestigationProposal",
    "Selection",
    "select_investigations",
    "proposal_is_admissible",
]


class GapKind(StrEnum):
    """The gap an investigation targets. Mirrors the plan's GAP-1..GAP-7."""

    PATHWAY = "pathway"
    EVALUATOR_NOISE = "evaluator_noise"
    DIVERSITY = "diversity"
    BUDGET_PROTOCOL = "budget_protocol"
    EVIDENCE_CONVERGENCE = "evidence_convergence"
    LOOP_HEALTH = "loop_health"
    OTHER = "other"


#: Phrases that signal a refutation condition rather than a wish. A "falsifiable
#: by" that contains none of these is a stated hope, not a test.
_FALSIFIABLE_MARKERS = (
    "reject",
    "disprove",
    "refute",
    "would fail",
    "would not",
    "assert",
    "measure",
    "compare",
    "threshold",
    "below",
    "above",
    "differ",
    "reduce",
    "increase",
    "hold",
    "not improve",
    "no change",
)


@dataclass(frozen=True)
class InvestigationProposal:
    """A candidate thing to investigate.

    ``falsifiable_by`` is the load-bearing field. It must state a *measurement*
    whose outcome would refute the proposal — which is what separates an
    investigation from an enthusiasm.
    """

    question: str
    resolves_gap: GapKind
    expected_information_gain: float
    cost: BudgetUnit
    falsifiable_by: str
    why_now: str = ""

    def __post_init__(self) -> None:
        if not (self.question or "").strip():
            raise ValueError("question must be a non-empty string")
        if not 0.0 <= self.expected_information_gain <= 1.0:
            raise ValueError(f"expected_information_gain must be within [0.0, 1.0], got {self.expected_information_gain!r}")

    @property
    def falsifiable(self) -> bool:
        """Whether ``falsifiable_by`` states something a measurement could refute."""
        text = (self.falsifiable_by or "").strip().lower()
        if not text:
            return False
        return any(marker in text for marker in _FALSIFIABLE_MARKERS)

    @property
    def cost_total(self) -> int:
        return self.cost.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "resolves_gap": self.resolves_gap.value,
            "expected_information_gain": round(self.expected_information_gain, 6),
            "cost": self.cost.to_dict(),
            "cost_total": self.cost_total,
            "falsifiable_by": self.falsifiable_by,
            "falsifiable": self.falsifiable,
            "why_now": self.why_now,
        }


@dataclass(frozen=True)
class Selection:
    """Which proposals are worth running, and why the rest are not."""

    admitted: tuple[InvestigationProposal, ...]
    rejected: tuple[tuple[InvestigationProposal, str], ...]
    reasons: list[str] = field(default_factory=list)

    @property
    def admitted_questions(self) -> list[str]:
        return [proposal.question for proposal in self.admitted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": [proposal.to_dict() for proposal in self.admitted],
            "rejected": [{"proposal": proposal.to_dict(), "reason": reason} for proposal, reason in self.rejected],
            "reasons": list(self.reasons),
        }


def proposal_is_admissible(
    proposal: InvestigationProposal,
    *,
    convergence: ConvergedVerdict | None = None,
    min_information_gain: float = 0.0,
) -> tuple[bool, str]:
    """Whether one proposal may consume budget.

    Refusals, in severity order:

    1. not falsifiable — an untestable investigation is an enthusiasm;
    2. below the information-gain floor;
    3. the *selection itself* is unverified (Phase E convergence).

    The third is the interesting one. Choosing what to investigate is the
    judgment the research says agents are bad at, so a proposal selected on an
    unverified self-assessment is exactly the claim Alpha's other subsystems
    exist to refuse.
    """
    if not proposal.falsifiable:
        return False, ("falsifiable_by does not state a measurement that could refute this proposal; an untestable investigation is an enthusiasm, not a research direction")
    if proposal.expected_information_gain < min_information_gain:
        return False, (f"expected information gain {proposal.expected_information_gain:.4f} is below the floor {min_information_gain:.4f}")
    if convergence is not None and convergence.status is not ConvergenceStatus.APPROVED:
        return False, (f"the selection itself is {convergence.status.value} ({convergence.reason}); an investigation chosen on an unverified self-assessment is the failure mode this module exists to prevent")
    return True, "falsifiable, above the gain floor, and the selection is converged"


def _priority(proposal: InvestigationProposal) -> float:
    """Information gain per unit budget.

    The denominator has a ``+1`` for the same reason the expert scorer does: a
    zero-cost proposal must not divide by zero, and a cost-free investigation
    should legitimately outrank an expensive one of equal value.
    """
    return proposal.expected_information_gain / (1 + proposal.cost_total)


def select_investigations(
    proposals: Iterable[InvestigationProposal],
    *,
    convergence: ConvergedVerdict | None = None,
    limit: int = 3,
    min_information_gain: float = 0.0,
    admission: bool = True,
) -> Selection:
    """Rank and admit investigations.

    Args:
        admission: When ``False``, everything is ranked but nothing is admitted.
            This is the dry-run mode: "what *would* you investigate?" answered
            without committing budget.

    Returns:
        A :class:`Selection`. Rejections carry their reason, so "why was my
        proposal dropped?" is always answerable.
    """
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")

    rejected: list[tuple[InvestigationProposal, str]] = []
    eligible: list[tuple[float, InvestigationProposal]] = []
    reasons: list[str] = []

    for proposal in proposals:
        ok, reason = proposal_is_admissible(
            proposal,
            convergence=convergence,
            min_information_gain=min_information_gain,
        )
        if ok:
            eligible.append((_priority(proposal), proposal))
        else:
            rejected.append((proposal, reason))

    # Deterministic: priority desc, then cost asc, then question. Ties broken on
    # a stable key so the same inputs always produce the same order.
    eligible.sort(key=lambda item: (-item[0], item[1].cost_total, item[1].question))

    if not eligible:
        reasons.append(f"no proposal was admissible out of {len(rejected)} rejected")

    if not admission:
        return Selection(admitted=(), rejected=tuple(rejected), reasons=[*reasons, "admission is disabled; nothing was admitted"])

    admitted = tuple(proposal for _priority_value, proposal in eligible[:limit])
    if len(eligible) > limit:
        reasons.append(f"{len(eligible)} admissible proposals; the top {limit} by information gain per budget were admitted")

    return Selection(admitted=admitted, rejected=tuple(rejected), reasons=reasons)
