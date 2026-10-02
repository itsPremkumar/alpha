"""The measurements that decide whether any of this helped.

## Why these and not pass rate

Pass rate is provably blind to the two failure modes this package targets.

The reuse audit ran 3,000 turns across two harnesses and four models and found
duplicated logic accumulating in **50.8% of task chains by turn 5 while pass rates
barely moved**. An agent can solve every task correctly and still degrade the
codebase, and a correctness-only instrument reports those runs as healthy. The
duplication number is therefore a first-class metric here rather than a
diagnostic extra.

Likewise, claim-level auditing found that **36.9% of successful trajectories
contain process errors**. A run that succeeded is not a run that was right, so
"did it pass" cannot stand in for "were its premises supported".

## The metric that explains the rest

The audit separates three numbers that are usually collapsed into one:

* **recall** — did the step read the prior art at all?
* **reuse** — having read it, did it build on it?
* **duplication** — did it rewrite the logic anyway?

The split is the point. When recall is high and reuse is low, the problem is
disposition, and no amount of extra context fixes it — handing the model the full
source of its own earlier work moved self-reuse to 29.2%, no better than giving
it nothing. That diagnosis is invisible if the three are averaged.

## Honesty rules for this module

* ``None`` means *not measurable*, never ``0``. A run that never invoked a reuse
  probe has no duplication rate; reporting ``0.0`` would claim the workspace is
  clean on the strength of never looking.
* ``audited=False`` is distinct from ``passed=False``. An unmeasured thing is not
  a passing thing.
* Every rate carries its numerator and denominator, so a 100% rate over 2 samples
  cannot be read as equivalent to one over 2,000.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.grounding.claims import ClaimLedger
from alpha.grounding.models import SupportStatus

__all__ = ["GroundingMetrics", "Origin", "ReuseLedger", "ReuseSample", "Rate"]


class Origin(StrEnum):
    """Whether a reuse target came from the repository or from the agent itself.

    Split because the two behave differently and one fix does not address both:
    repository recall collapses across turns (86.3% → 38.8%), while self-recall
    stays saturated above 98% and self-reuse still decays (83.9% → 69.1%). The
    second is a decision problem; the first is an attention problem.
    """

    REPOSITORY = "repository"
    SELF = "self"


@dataclass(frozen=True)
class Rate:
    """A ratio that carries its own sample size.

    ``value`` is ``None`` when the denominator is zero, never ``0.0``: "no
    samples" and "zero successes" are different facts and averaging them produces
    a number that describes neither.
    """

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "numerator": self.numerator, "denominator": self.denominator}


@dataclass
class ReuseSample:
    """One reuse decision for one target."""

    target: str
    origin: Origin
    #: The step's file reads covered at least one line of the target's body.
    recalled: bool
    #: The step actually called / imported the target.
    reused: bool
    #: The step rewrote the target's logic without calling it.
    reimplemented: bool = False

    def __post_init__(self) -> None:
        if self.reused and self.reimplemented:
            raise ValueError("a target cannot be both reused and re-implemented in the same step")


@dataclass
class ReuseLedger:
    """Accumulates reuse decisions and derives the three separate rates."""

    samples: list[ReuseSample] = field(default_factory=list)

    def record(
        self,
        target: str,
        origin: Origin | str,
        *,
        recalled: bool,
        reused: bool,
        reimplemented: bool = False,
    ) -> ReuseSample:
        sample = ReuseSample(
            target=target,
            origin=Origin(origin),
            recalled=recalled,
            reused=reused,
            reimplemented=reimplemented,
        )
        self.samples.append(sample)
        return sample

    def _of(self, origin: Origin) -> list[ReuseSample]:
        return [s for s in self.samples if s.origin is origin]

    def recall_rate(self, origin: Origin) -> Rate:
        subset = self._of(origin)
        return Rate(sum(1 for s in subset if s.recalled), len(subset))

    def reuse_rate(self, origin: Origin) -> Rate:
        subset = self._of(origin)
        return Rate(sum(1 for s in subset if s.reused), len(subset))

    def missed_sight(self, origin: Origin) -> tuple[str, ...]:
        """Targets that were never read.

        An *exploration* gap. More search or a better index is the candidate fix.
        """
        return tuple(sorted(s.target for s in self._of(origin) if not s.recalled))

    def saw_and_ignored(self, origin: Origin) -> tuple[str, ...]:
        """Targets that were read and then not built on.

        An *execution-side* gap, and the more interesting one: the information was
        available and the disposition was wrong. This is the number the interface-
        memory ablation moved, and the one extra context does not.
        """
        return tuple(sorted(s.target for s in self._of(origin) if s.recalled and not s.reused))

    def duplicated_targets(self) -> tuple[str, ...]:
        return tuple(sorted(s.target for s in self.samples if s.reimplemented))

    def duplication_rate(self) -> Rate:
        targets = {s.target for s in self.samples}
        duplicated = set(self.duplicated_targets())
        return Rate(len(duplicated), len(targets))

    def diagnosis(self) -> str:
        """One word naming which failure this is.

        Worth having as a single value because the two diagnoses lead to opposite
        instructions: ``exploration_gap`` means search harder, ``disposition_gap``
        means the agent saw it and chose otherwise — and the measured fix for a
        disposition gap is a compact interface map, not more reading.
        """
        for origin in (Origin.REPOSITORY, Origin.SELF):
            if self.saw_and_ignored(origin):
                return "disposition_gap"
        if any(self.missed_sight(origin) for origin in (Origin.REPOSITORY, Origin.SELF)):
            return "exploration_gap"
        return "clean"


@dataclass
class GroundingMetrics:
    """Aggregate report for one scope."""

    scope: str = "default"
    reuse: ReuseLedger = field(default_factory=ReuseLedger)
    #: Set when a reuse probe actually ran. Absent probe -> no duplication claim.
    reuse_probed: bool = False
    #: Ledger snapshots accumulated across steps, for cross-turn contradiction.
    ledger: ClaimLedger | None = None

    # -- claims ----------------------------------------------------------

    def claim_support(self) -> dict[str, Rate]:
        if self.ledger is None or len(self.ledger) == 0:
            return {}
        claims = self.ledger.all()
        return {
            "direct": Rate(sum(1 for c in claims if c.support is SupportStatus.DIRECT), len(claims)),
            "authorized": Rate(sum(1 for c in claims if c.authorized), len(claims)),
            "consequential_blocked": Rate(len(self.ledger.blocking()), sum(1 for c in claims if c.consequential)),
        }

    def contradictions(self) -> int:
        return len(self.ledger.conflicts()) if self.ledger is not None else 0

    # -- report ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The report.

        Every rate that could not be measured is ``None`` with a denominator of 0.
        ``reuse_probed`` travels beside the duplication rate so a reader can tell
        "no duplication" from "nothing looked".
        """
        report: dict[str, Any] = {
            "scope": self.scope,
            "reuse_probed": self.reuse_probed,
            "reuse": {
                origin.value: {
                    "recall": self.reuse.recall_rate(origin).to_dict(),
                    "reuse": self.reuse.reuse_rate(origin).to_dict(),
                    "missed_sight": list(self.reuse.missed_sight(origin)),
                    "saw_and_ignored": list(self.reuse.saw_and_ignored(origin)),
                }
                for origin in (Origin.REPOSITORY, Origin.SELF)
            },
            "claims": {k: v.to_dict() for k, v in self.claim_support().items()},
            "contradictions": self.contradictions(),
        }
        # Only a duplication *rate* when something was actually looked for.
        report["duplication"] = self.reuse.duplication_rate().to_dict() if self.reuse_probed else Rate(0, 0).to_dict()
        report["duplication_measured"] = self.reuse_probed
        report["diagnosis"] = self.reuse.diagnosis() if self.reuse_probed else "unmeasured"
        report["blocking_claims"] = [c.claim_id for c in self.ledger.blocking()] if self.ledger else []
        return report

    def merge(self, other: GroundingMetrics) -> GroundingMetrics:
        """Combine two scopes, e.g. a resumed run plus its recovery prefix."""
        return GroundingMetrics(
            scope=self.scope,
            reuse=ReuseLedger(samples=[*self.reuse.samples, *other.reuse.samples]),
            reuse_probed=self.reuse_probed or other.reuse_probed,
            ledger=self.ledger or other.ledger,
        )


def gate_summary(results: Iterable[Any]) -> dict[str, Any]:
    """Re-export of the gate tally, so a caller needs one import for the report."""
    from alpha.grounding.gates import summarize

    return summarize(results)


def format_rate(rate: Rate, *, as_percent: bool = True) -> str:
    """Human-readable rate that refuses to invent a number.

    ``"n/a (0 samples)"`` rather than ``"0%"`` — the difference matters most at
    the moment someone is deciding whether the system is working.
    """
    if rate.value is None:
        return "n/a (0 samples)"
    if as_percent:
        return f"{rate.value * 100:.1f}% ({rate.numerator}/{rate.denominator})"
    return f"{rate.value:.3f} ({rate.numerator}/{rate.denominator})"


def combine(samples: Sequence[ReuseSample]) -> Rate:
    """Pool samples into one rate, keeping the numerator honest."""
    return Rate(sum(1 for s in samples if s.reused), len(samples))
