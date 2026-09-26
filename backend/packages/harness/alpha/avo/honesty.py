"""Honesty metrics for a self-improving loop.

Three facts about evolutionary search that a throughput curve hides, taken from
AVO's own trajectory analysis:

* **500+ internal candidate directions produced 40 commits** -- roughly twelve
  failed-or-abandoned attempts per committed version. The committed trajectory is
  the residue, not the work.
* **Progress is discrete, not gradual.** Five architectural inflection points
  (v8, v13, v20, v30, v33) separated by plateaus; the other thirty-five versions
  are smaller compounding refinement. On a curve those two are the same shape.
* **Diminishing returns are real.** v1-v20 carried the largest absolute gains;
  v21-v40 were smaller but compounding.

The rule this module enforces is the one that makes the other two usable:

    **No self-improvement metric reports an improvement without its denominator
    beside it.**

Every gain in :meth:`HonestyReport.to_dict` is a dict with ``absolute``,
``denominator_commits`` and ``failed_attempts`` keys. There is no code path that
produces a bare improvement number. :func:`assert_gains_carry_denominators` turns
that from a convention into an assertion, and a test calls it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import ChangeKind, RejectionCategory

__all__ = [
    "HonestyReport",
    "assert_gains_carry_denominators",
    "build_honesty_report",
]


#: AVO's measured shape. Used as the comparison baseline, never as a claim about
#: alpha -- alpha has not run a seven-day search.
AVO_REFERENCE_RATIO = 12.0


@dataclass
class CommitStep:
    """One committed version and what it actually did, as opposed to how much it gained."""

    version_id: str
    kind: str
    score: float
    previous_best: float
    best_committed_id: str | None
    invariant_scope: str = "not_run"
    author: str = "unknown"

    @property
    def absolute_gain(self) -> float:
        return round(self.score - self.previous_best, 6)


@dataclass
class HonestyReport:
    """The denominator-bearing report of one search run."""

    total_explored: int = 0
    committed_steps: list[CommitStep] = field(default_factory=list)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    unverified_invariant_commits: int = 0
    behaviour_changed_commits: int = 0
    scorer_change_refusals: int = 0
    redirects: int = 0

    # -- denominators ---------------------------------------------------
    @property
    def committed_count(self) -> int:
        return len(self.committed_steps)

    @property
    def failed_count(self) -> int:
        return max(0, self.total_explored - self.committed_count)

    @property
    def failed_to_committed(self) -> float:
        """The AVO ratio. ``inf`` when nothing has committed -- which is itself the answer."""
        if not self.committed_steps:
            return float("inf")
        return round(self.failed_count / self.committed_count, 3)

    @property
    def commit_rate(self) -> float:
        if not self.total_explored:
            return 0.0
        return round(self.committed_count / self.total_explored, 4)

    # -- shape of progress ----------------------------------------------
    @property
    def change_kind_histogram(self) -> dict[str, int]:
        histogram = {kind.value: 0 for kind in ChangeKind}
        for step in self.committed_steps:
            histogram[step.kind] = histogram.get(step.kind, 0) + 1
        return histogram

    @property
    def inflection_points(self) -> list[dict[str, Any]]:
        """Structural discoveries. AVO found five among forty versions."""
        return [
            {"version_id": s.version_id, "score": s.score, "gain": s.absolute_gain, "kind": s.kind}
            for s in self.committed_steps
            if s.kind == ChangeKind.STRUCTURAL.value
        ]

    @property
    def diminishing_returns(self) -> dict[str, Any]:
        """Marginal gain per commit, plus a plain statement of the curve's shape.

        Three states are distinguished, because collapsing them is the failure
        this exists to prevent:

        * ``plateaued`` -- mean second-half gain is zero or negative. Progress
          has *stopped*. Report this instead of the last delta.
        * ``diminishing`` -- gains are shrinking but still positive. Real
          progress, smaller than before (AVO's v21-v40).
        * neither -- gains held. Steady progress, not a plateau.

        A constant positive gain is emphatically **not** a plateau, and calling it
        one would be its own kind of dishonesty.
        """
        gains = [s.absolute_gain for s in self.committed_steps]
        if len(gains) < 4:
            return {
                "commits": len(gains),
                "marginal_gains": gains,
                "mean_gain_first_half": None,
                "mean_gain_second_half": None,
                "plateaued": None,
                "diminishing": None,
                "plateau_reason": "not enough committed versions to distinguish a plateau from noise",
            }
        half = len(gains) // 2
        first = gains[:half]
        second = gains[half:]
        mean_first = round(sum(first) / len(first), 6)
        mean_second = round(sum(second) / len(second), 6)
        plateaued = mean_second <= 0.0
        diminishing = (not plateaued) and mean_second < mean_first
        if plateaued:
            reason = (
                f"PLATEAUED: mean marginal gain across the first {half} commits was {mean_first:.6g} and across "
                f"the last {len(gains) - half} it is {mean_second:.6g}. Progress has stopped over "
                f"{self.total_explored} explored directions and {self.committed_count} commits."
            )
        elif diminishing:
            reason = (
                f"DIMINISHING: mean marginal gain fell from {mean_first:.6g} to {mean_second:.6g}. Progress is "
                f"real but shrinking, across {self.committed_count} commits over {self.total_explored} explored directions."
            )
        else:
            reason = (
                f"STEADY: mean marginal gain held at {mean_first:.6g} -> {mean_second:.6g} across "
                f"{self.committed_count} commits over {self.total_explored} explored directions."
            )
        return {
            "commits": len(gains),
            "marginal_gains": gains,
            "mean_gain_first_half": mean_first,
            "mean_gain_second_half": mean_second,
            "plateaued": plateaued,
            "diminishing": diminishing,
            "plateau_reason": reason,
        }

    # -- the rule --------------------------------------------------------
    def gains_with_denominators(self) -> list[dict[str, Any]]:
        """Every gain, each with the denominator it was measured against."""
        return [
            {
                "version_id": step.version_id,
                "kind": step.kind,
                "absolute": step.absolute_gain,
                "score": step.score,
                "previous_best": step.previous_best,
                "denominator_commits": self.committed_count,
                "denominator_explored": self.total_explored,
                "failed_attempts": self.failed_count,
                "invariant_scope": step.invariant_scope,
            }
            for step in self.committed_steps
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "denominators": {
                "total_explored": self.total_explored,
                "committed": self.committed_count,
                "failed": self.failed_count,
                "failed_to_committed": self.failed_to_committed,
                "failed_to_committed_infinite": self.committed_count == 0,
                "commit_rate": self.commit_rate,
                "avo_reference_failed_to_committed": AVO_REFERENCE_RATIO,
            },
            "change_kinds": self.change_kind_histogram,
            "inflection_points": self.inflection_points,
            "diminishing_returns": self.diminishing_returns,
            "gains": self.gains_with_denominators(),
            "rejections_by_reason": dict(self.rejection_counts),
            "caveats": {
                "commits_with_unmeasured_invariants": self.unverified_invariant_commits,
                "commits_with_declared_behaviour_change": self.behaviour_changed_commits,
                "invariant_verified_commits": max(
                    0,
                    self.committed_count - self.unverified_invariant_commits - self.behaviour_changed_commits,
                ),
                "scorer_change_refusals": self.scorer_change_refusals,
                "supervisor_redirects": self.redirects,
            },
        }


def assert_gains_carry_denominators(report: HonestyReport) -> None:
    """Fail loudly if any gain is reported without the population it came from.

    This is the checkable form of "no self-improvement metric may report
    improvement without its denominator beside it".
    """
    if report.committed_count and not report.total_explored >= report.committed_count:
        raise AssertionError(
            f"honesty report is inconsistent: {report.committed_count} commits claimed from only {report.total_explored} explored directions"
        )
    for gain in report.gains_with_denominators():
        for key in ("absolute", "denominator_commits", "denominator_explored", "failed_attempts"):
            if key not in gain:
                raise AssertionError(f"gain for {gain.get('version_id')} is missing '{key}'; a bare improvement number is not reportable")
    if report.total_explored == 0 and report.committed_count == 0:
        raise AssertionError("honesty report claims no exploration and no commits; it should report that it has nothing to say")


def build_honesty_report(
    *,
    total_explored: int,
    committed_steps: list[CommitStep],
    rejection_counts: dict[str, int] | None = None,
    unverified_invariant_commits: int = 0,
    behaviour_changed_commits: int = 0,
    scorer_change_refusals: int = 0,
    redirects: int = 0,
) -> HonestyReport:
    """Assemble a report. Kept separate from the class so callers cannot half-fill one."""
    report = HonestyReport(
        total_explored=total_explored,
        committed_steps=list(committed_steps),
        rejection_counts=dict(rejection_counts or {}),
        unverified_invariant_commits=unverified_invariant_commits,
        behaviour_changed_commits=behaviour_changed_commits,
        scorer_change_refusals=scorer_change_refusals,
        redirects=redirects,
    )
    if report.rejection_counts:
        unknown = set(report.rejection_counts) - {c.value for c in RejectionCategory}
        if unknown:
            raise ValueError(f"rejection counts use unknown categories: {sorted(unknown)}")
    return report
