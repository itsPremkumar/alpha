"""The attempt budget: a fixed number, decremented per attempt, remainder visible.

The budget is the whole reason a self-repair loop is safe to run unattended. It
is a *ledger*, not a limit checked at the top of a ``while`` — the difference
matters, and it is the difference between a guard and a hope:

* :meth:`AttemptBudget.consume` raises when the budget is already spent, so a
  caller cannot overdraw it even if the loop structure around it is wrong.
* :attr:`AttemptBudget.remaining` is a first-class value the controller puts in
  the repair prompt and in the report, so "how many tries are left" is never a
  private fact of the loop.

Exhaustion is a *state*, not an exception and not a give-up. When the budget
runs out mid-loop the controller keeps the last determinate reading, reports
``UNVERIFIED`` with reason ``budget_exhausted``, and says in the report that the
last recorded run did or did not pass. The alternative — reporting FAILED
because we stopped trying — is how an unfinished investigation becomes a
decided verdict, and this repository treats exactly that confusion as the defect
worth building against.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AttemptBudget"]


@dataclass
class AttemptBudget:
    """A fixed allowance of repair attempts.

    The initial verification run does **not** consume an attempt: the budget
    bounds *repairs*, and a loop that cannot verify at all is bounded by its own
    single execution.
    """

    total: int
    spent: int = 0

    def __post_init__(self) -> None:
        if self.total < 1:
            raise ValueError(f"attempt budget must allow at least one attempt, got {self.total}")
        if self.spent < 0:
            raise ValueError(f"attempts spent cannot be negative, got {self.spent}")
        if self.spent > self.total:
            raise ValueError(f"attempts spent ({self.spent}) cannot exceed the budget ({self.total})")

    @property
    def remaining(self) -> int:
        """Attempts still available. Never negative."""
        return max(0, self.total - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def consume(self) -> int:
        """Spend one attempt and return the new remainder.

        Raises ``ValueError`` on an exhausted budget. The controller checks
        :attr:`exhausted` before every dispatch, so this raise is a backstop
        against a future edit that forgets the check — it is the difference
        between "the loop stopped" and "the loop overran".
        """
        if self.exhausted:
            raise ValueError(f"attempt budget of {self.total} is exhausted")
        self.spent += 1
        return self.remaining
