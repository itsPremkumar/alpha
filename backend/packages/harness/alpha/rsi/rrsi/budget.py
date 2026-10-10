"""Regularizer **P1** — the annealed per-round edit budget.

RRSI's proposal-side first regularizer caps *how much of the harness a single
round may rewrite*. The cap anneals: a round early in the search is allowed a
large edit budget, and the budget decays to ``b_min`` as the horizon ``T``
approaches, so late rounds may only tune rather than restructure.

    ``b_t = ⌈ b_min + (b_max − b_min) · ½ (1 + cos(π · t / T)) ⌉``

Binding rules:

* **The schedule saturates past ``T``; it never re-widens.** For ``t > T`` the
  cosine rises again, which would hand a long-running search a *larger* budget
  than it had at the start — the exact inversion of a decaying budget. The
  argument is therefore pinned to ``T`` for every ``t ≥ T``, and the returned
  outcome says ``saturated`` so the caller can see the horizon was passed.
* **Truncation is disclosed, never silent.** :func:`apply_edit_budget` returns
  the kept edits, the dropped edits and the budget that caused the drop. A
  caller that silently lost half a payload could not tell "the budget trimmed
  my proposal" from "the template produced a short proposal".
* **No edit is re-ordered or rewritten.** The budget is a prefix cut over the
  caller's edit order, which is itself the proposer's declaration of priority.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from alpha.rsi.rrsi.config import RrsiParams

__all__ = ["BudgetOutcome", "apply_edit_budget", "edit_budget"]


@dataclass(frozen=True)
class BudgetOutcome:
    """The result of applying one round's edit budget.

    ``kept``/``dropped`` preserve the caller's order. ``saturated`` records
    that ``round_index`` reached or passed ``T`` (see the module docstring);
    it is a fact about the schedule, not a measurement of the candidate.
    """

    budget: int
    round_index: int
    kept: tuple[str, ...]
    dropped: tuple[str, ...]
    saturated: bool
    reason: str

    @property
    def applied(self) -> bool:
        """Whether any edit was removed by this budget."""
        return bool(self.dropped)

    def to_dict(self) -> dict[str, object]:
        return {
            "budget": self.budget,
            "round_index": self.round_index,
            "kept": list(self.kept),
            "dropped": list(self.dropped),
            "dropped_count": len(self.dropped),
            "saturated": self.saturated,
            "applied": self.applied,
            "reason": self.reason,
        }


def edit_budget(round_index: int, params: RrsiParams | None = None) -> int:
    """Return round ``round_index``'s edit budget ``b_t``.

    ``round_index`` is refused below zero (a negative round is a caller bug,
    and cos() over a negative argument would *start* the anneal mid-descent).
    Past ``T`` the schedule saturates at ``b_min`` rather than oscillating —
    see the module docstring.
    """
    params = (params or RrsiParams()).validate()
    if isinstance(round_index, bool) or not isinstance(round_index, int):
        raise ValueError(f"round_index must be an integer; got {round_index!r}.")
    if round_index < 0:
        raise ValueError(f"round_index must be >= 0; got {round_index}.")
    # Saturation is expressed by clamping the phase argument, not by a branch:
    # for t >= T the cosine stays at its terminal value, so the schedule sits
    # at b_min and never re-widens (see the module docstring).
    t = min(round_index, params.rounds)
    # cos(pi * t / T): 1 at t=0 (full budget), -1 at t=T (floor budget).
    phase = 0.5 * (1.0 + math.cos(math.pi * t / params.rounds))
    value = params.budget_min + (params.budget_max - params.budget_min) * phase
    return max(params.budget_min, math.ceil(value))


def apply_edit_budget(edits: Sequence[str], round_index: int, params: RrsiParams | None = None) -> BudgetOutcome:
    """Cut ``edits`` down to this round's budget, reporting exactly what went.

    The cut is a prefix: edit *i* of the caller's list is kept iff
    ``i < budget``. Nothing is reordered, rewritten or invented, so an empty
    ``kept`` with a non-empty ``edits`` means the budget itself was 0 — which
    :func:`edit_budget` cannot produce, making that combination a caller error
    worth seeing rather than hiding.
    """
    if isinstance(edits, (str, bytes)):
        raise ValueError("edits must be a sequence of edit strings, not a single string.")
    budget = edit_budget(round_index, params)
    ordered = tuple(str(edit) for edit in edits)
    kept, dropped = ordered[:budget], ordered[budget:]
    saturated = round_index >= (params or RrsiParams()).rounds
    if dropped:
        reason = f"edit budget b_{round_index} = {budget} retained {len(kept)} of {len(ordered)} proposed edit(s); {len(dropped)} dropped in proposal order."
    else:
        reason = f"edit budget b_{round_index} = {budget} retained all {len(ordered)} proposed edit(s)."
    if saturated:
        reason += f" Round {round_index} is at/past the horizon T = {(params or RrsiParams()).rounds}, so the schedule is saturated at b_min."
    return BudgetOutcome(budget=budget, round_index=round_index, kept=kept, dropped=dropped, saturated=saturated, reason=reason)
