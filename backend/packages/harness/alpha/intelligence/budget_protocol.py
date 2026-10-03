"""Phase D — equal-budget comparison: make cross-generation claims falsifiable.

The claim this refuses
----------------------
"Generation 7 is better than generation 3." Alpha can currently produce that
sentence. It also cannot distinguish it from "generation 7 was given seven times
the attempts".

*The Last AI Built by Humans* (arXiv 2609.11873) defines the standard that
matters:

> **Effective recursion** means the inherited mechanism improves *later
> successors* under **comparable budgets** and independent assessment.

Without a matched-budget protocol, no claim of effective recursion is
falsifiable. That is why this module's primary behaviour is to **refuse**.

Why attempts and not seconds
---------------------------
The budget unit is **attempts**, deliberately. Wall-clock is not comparable
across machine loads, and token counts are not comparable across providers and
model sizes. The attempt count is the one unit a human and a loop can both count
honestly, which is the property that makes the resulting claim meaningful.

Refusal is the feature
----------------------
:func:`compare_matched` does not return "delta 0.04, close enough". It refuses
and states by how much the budgets differ, so a caller cannot quote an
unfalsifiable number. ``BudgetMismatchError`` is raised, not caught and
downgraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BudgetUnit",
    "MatchedBudgetRun",
    "BudgetComparison",
    "BudgetMismatchError",
    "compare_matched",
    "is_comparable",
]


class BudgetMismatchError(ValueError):
    """Two runs were compared at materially different budgets.

    Raised rather than returned as a flag, because a caller that receives a
    boolean can ignore it and a caller that receives an exception must deal with
    it. This is the whole mechanism that stops an unfalsifiable claim.
    """


@dataclass(frozen=True)
class BudgetUnit:
    """The resources one candidate consumed.

    ``tokens`` is ``None`` when not instrumented, never ``0.0`` — "we did not
    measure tokens" and "this candidate used no tokens" are opposite claims, and
    the first is the honest one in most deployments.
    """

    attempts: int
    tools: int
    tokens: int | None = None

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError(f"attempts must be >= 0, got {self.attempts}")
        if self.tools < 0:
            raise ValueError(f"tools must be >= 0, got {self.tools}")
        if self.tokens is not None and self.tokens < 0:
            raise ValueError(f"tokens must be >= 0 or None, got {self.tokens}")

    @property
    def total(self) -> int:
        return self.attempts + self.tools

    def to_dict(self) -> dict[str, Any]:
        return {"attempts": self.attempts, "tools": self.tools, "tokens": self.tokens, "total": self.total}


@dataclass(frozen=True)
class MatchedBudgetRun:
    """One candidate's outcome at a stated budget."""

    label: str
    generation: int
    score: float
    budget: BudgetUnit
    evidence_kind: str = "measured"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generation": self.generation,
            "score": round(self.score, 6),
            "budget": self.budget.to_dict(),
            "evidence_kind": self.evidence_kind,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class BudgetComparison:
    """A verdict that is only reachable at matched budgets."""

    better: str
    delta: float
    per_attempt_gain: float
    budgets_matched: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "better": self.better,
            "delta": round(self.delta, 6),
            "per_attempt_gain": round(self.per_attempt_gain, 8),
            "budgets_matched": dict(self.budgets_matched),
            "reasons": list(self.reasons),
        }


def is_comparable(
    left: BudgetUnit,
    right: BudgetUnit,
    *,
    tolerance: float = 0.0,
) -> tuple[bool, list[str]]:
    """Whether two budgets are close enough for a claim to mean anything.

    ``tolerance`` is a **fraction of the larger budget**. ``0.0`` demands exact
    equality, which is the honest default: a tolerance is a licence to compare
    unequal things, so it has to be a deliberate operator decision rather than a
    default that quietly launders a mismatch into a result.
    """
    problems: list[str] = []
    for name in ("attempts", "tools"):
        left_value, right_value = getattr(left, name), getattr(right, name)
        denominator = max(left_value, right_value, 1)
        drift = abs(left_value - right_value) / denominator
        if drift > tolerance:
            problems.append(f"{name} differ by {drift:.1%} ({left_value} vs {right_value}), beyond the tolerance {tolerance:.1%}")
    if (left.tokens is None) != (right.tokens is None):
        problems.append("token usage is instrumented on one side only, so the runs cannot be compared on cost")
    elif left.tokens is not None and right.tokens is not None:
        denominator = max(left.tokens, right.tokens, 1)
        drift = abs(left.tokens - right.tokens) / denominator
        if drift > tolerance:
            problems.append(f"tokens differ by {drift:.1%} ({left.tokens} vs {right.tokens}), beyond the tolerance {tolerance:.1%}")
    return (not problems), problems


def compare_matched(
    left: MatchedBudgetRun,
    right: MatchedBudgetRun,
    *,
    tolerance: float = 0.0,
    minimum_attempts: int = 1,
) -> BudgetComparison:
    """Compare two runs **only** if their budgets match.

    Raises:
        BudgetMismatchError: when the budgets differ beyond ``tolerance``, or
            when either run's evidence is unverified. An unverified score cannot
            be compared against anything, so it is refused here for the same
            reason an unmatched budget is.

    Returns:
        A :class:`BudgetComparison` naming the better run and the **per-attempt**
        gain — the efficiency figure the research standard actually asks for,
        because a higher raw score bought with more attempts is not recursion.
    """
    comparable, problems = is_comparable(left.budget, right.budget, tolerance=tolerance)
    if not comparable:
        raise BudgetMismatchError(f"{left.label!r} (gen {left.generation}) and {right.label!r} (gen {right.generation}) cannot be compared: " + "; ".join(problems) + ". Re-run both at a matched budget, or raise `tolerance` deliberately.")

    for run in (left, right):
        if run.evidence_kind != "measured":
            raise BudgetMismatchError(f"{run.label!r} has evidence_kind {run.evidence_kind!r}; an unmeasured score cannot be compared against another candidate ({run.detail.get('reason', 'no reason recorded')})")
        if run.budget.attempts < minimum_attempts:
            raise BudgetMismatchError(f"{run.label!r} ran {run.budget.attempts} attempt(s); a per-attempt gain needs at least {minimum_attempts}. Zero-attempt runs are not evidence of efficiency.")

    delta = right.score - left.score
    attempts = max(left.budget.attempts, right.budget.attempts)
    per_attempt_gain = delta / attempts
    better = "tie"
    reasons: list[str] = [f"both runs consumed {attempts} attempt(s), so the comparison is budget-matched"]
    if delta > 0:
        better = right.label
    elif delta < 0:
        better = left.label
    else:
        reasons.append("the scores are identical, so no generation is better on this evidence")

    return BudgetComparison(
        better=better,
        delta=delta,
        per_attempt_gain=per_attempt_gain,
        budgets_matched={"attempts": left.budget.attempts, "tools": left.budget.tools},
        reasons=reasons,
    )
