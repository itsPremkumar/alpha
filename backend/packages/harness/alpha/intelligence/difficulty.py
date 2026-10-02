"""Difficulty estimation and adaptive compute allocation (prompt §26/§27).

What already exists, and what this adds
---------------------------------------
Alpha has three pieces of this:

* :class:`alpha.reasoning.governor.ReasoningGovernor` maps a prompt to a
  reasoning tier — but from **prompt text alone**, heuristically.
* :class:`alpha.reasoning.budget.ReasoningBudgetLedger` enforces a
  multi-dimensional budget — but it does not decide how much to allocate.
* :mod:`alpha.deliberation` and :mod:`alpha.learning.curriculum` carry partial
  difficulty signals.

This module supplies the missing middle: a **multi-signal estimator** that
combines declared complexity, novelty, uncertainty, dependency count, tool
breadth, verification difficulty and historical failure rate into one normalised
score, and a **compute plan** that turns that score into a bounded allocation.
It then hands that plan to the *existing* governor and budget rather than
replacing them.

Why the signals are optional and honest
---------------------------------------
Every signal is ``None`` when not supplied, and an unknown signal is **excluded
from the weighted mean and renormalised** rather than defaulted to zero. The
alternative — treating "unknown" as "easy" — is how a task with seven measured
complexity dimensions and no failure history gets routed as trivial. The
returned :attr:`DifficultyEstimate.coverage` reports how much of the signal set
was actually measured, so a score built from one signal is visibly thin.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "DifficultyBand",
    "ComputeStep",
    "ComputePlan",
    "DifficultySignals",
    "DifficultyEstimate",
    "DifficultyEstimator",
    "should_continue_reasoning",
    "ContinueDecision",
    "DEFAULT_BANDS",
    "DEFAULT_SIGNAL_WEIGHTS",
]


class DifficultyBand(StrEnum):
    """The four bands the prompt describes."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"


#: Upper bound of each band, in normalised 0..1 difficulty. The final band is
#: implicit (anything at or above the last bound).
DEFAULT_BANDS: dict[str, float] = {"easy": 0.25, "medium": 0.5, "hard": 0.75}

#: Signal weights. Chosen so ``historical_failure_rate`` is the heaviest
#: *single* evidence an operator already has, while ``declared_complexity`` is
#: the cheapest to supply.
DEFAULT_SIGNAL_WEIGHTS: dict[str, float] = {
    "declared_complexity": 0.15,
    "novelty": 0.15,
    "uncertainty": 0.15,
    "dependency_count": 0.15,
    "tool_breadth": 0.10,
    "verification_difficulty": 0.10,
    "historical_failure_rate": 0.20,
}


@dataclass(frozen=True)
class DifficultySignals:
    """The seven inputs. All optional; ``None`` means "not measured"."""

    declared_complexity: float | None = None
    """0..1. Already-known complexity, e.g. a user's ``/complex`` marker."""

    novelty: float | None = None
    """0..1. How unlike anything in the experience bank this task is."""

    uncertainty: float | None = None
    """0..1. Unresolved questions or conflicting evidence."""

    dependency_count: int | None = None
    """Raw count of sub-tasks/dependencies. Normalised via the divisor below."""

    tool_breadth: float | None = None
    """0..1. Fraction of distinct tool families the task will touch."""

    verification_difficulty: float | None = None
    """0..1. How hard it is to *know* the answer was right."""

    historical_failure_rate: float | None = None
    """0..1. Measured failure rate on comparable prior work."""

    dependency_divisor: float = 5.0
    """Normalisation for ``dependency_count``. 5 independent dependencies is
    treated as maximal complexity; raising it makes the same graph look easier.
    Configurable per call rather than hardcoded in the formula."""

    def measured(self) -> dict[str, float]:
        """Normalise the measured signals to 0..1, dropping the unmeasured ones."""
        out: dict[str, float] = {}
        for name in DEFAULT_SIGNAL_WEIGHTS:
            value = getattr(self, name, None)
            if value is None:
                continue
            if name == "dependency_count":
                divisor = max(1.0, float(self.dependency_divisor))
                out[name] = max(0.0, min(1.0, float(value) / divisor))
            else:
                out[name] = max(0.0, min(1.0, float(value)))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in DEFAULT_SIGNAL_WEIGHTS}


class ContinueDecision(StrEnum):
    """Prompt §27 outcomes."""

    STOP = "STOP"
    CONTINUE = "CONTINUE"
    CHANGE_APPROACH = "CHANGE_APPROACH"
    DELEGATE = "DELEGATE"
    VERIFY = "VERIFY"


def should_continue_reasoning(
    *,
    confidence: float | None,
    evidence_quality: float | None = None,
    unresolved_questions: int | None = None,
    critic_disagreement: bool | None = None,
    difficulty: float | None = None,
    remaining_budget: float | None = None,
    max_depth: int = 3,
    depth: int = 0,
) -> tuple[ContinueDecision, str]:
    """Decide the next reasoning move, with the reason.

    Ordering is deliberate and is the whole point of the function:

    1. **Depth/budget checks first.** A controller that can answer ``CONTINUE``
       after the budget is exhausted is not bounded, whatever it says about
       confidence.
    2. **``CHANGE_APPROACH`` before ``CONTINUE`` when critics disagree.** More
       passes on a contested answer is the wrong move; a different approach is.
    3. **``DELEGATE`` for high difficulty with budget but little depth.** Handing
       a very hard task to a specialist beats grinding one context.
    4. **``VERIFY`` when evidence is weak but the answer looks confident** — the
       dangerous combination.
    5. **``STOP`` when nothing above fires.** Stopping is the default because it
       is the only outcome that cannot spend unbounded budget.
    """
    if depth >= max_depth:
        return ContinueDecision.STOP, f"reasoning depth {depth} reached the configured maximum {max_depth}"
    if remaining_budget is not None and remaining_budget <= 0:
        return ContinueDecision.STOP, f"remaining reasoning budget is {remaining_budget:.4f}, which is exhausted"

    if critic_disagreement:
        if difficulty is not None and difficulty >= 0.75 and (remaining_budget is None or remaining_budget > 0):
            return ContinueDecision.DELEGATE, ("critics disagree on a very hard task and budget remains; delegating is a better move than another pass")
        return ContinueDecision.CHANGE_APPROACH, ("critics disagree on the current answer; another pass over the same approach is unlikely to converge, so change approach instead")

    if confidence is None:
        return ContinueDecision.STOP, "no confidence measurement is available, so there is no basis to spend budget"

    if evidence_quality is not None and evidence_quality < 0.4 and confidence >= 0.7:
        return ContinueDecision.VERIFY, (f"confidence {confidence:.2f} is high but evidence quality is only {evidence_quality:.2f}; the answer is confident and unsupported, so verify before continuing")

    if unresolved_questions is not None and unresolved_questions > 0 and confidence < 0.6:
        return ContinueDecision.CONTINUE, (f"{unresolved_questions} unresolved question(s) with confidence {confidence:.2f}; another pass is expected to help")

    if difficulty is not None and difficulty >= 0.75 and depth < max_depth:
        if remaining_budget is not None and remaining_budget <= 0:
            return ContinueDecision.STOP, "very hard task but the budget is exhausted"
        return ContinueDecision.DELEGATE, f"difficulty {difficulty:.2f} is in the very-hard band; delegate rather than deepen"

    if confidence < 0.5:
        return ContinueDecision.CONTINUE, f"confidence {confidence:.2f} is below the continue threshold of 0.50"
    return ContinueDecision.STOP, f"confidence {confidence:.2f} is at or above the stop threshold of 0.50"


@dataclass(frozen=True)
class ComputeStep:
    """One bounded stage in a compute plan."""

    name: str
    required: bool
    rationale: str
    bounded_by: str = ""
    """The budget dimension or limit that caps this step."""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required, "rationale": self.rationale, "bounded_by": self.bounded_by}


@dataclass(frozen=True)
class ComputePlan:
    """The allocation for one task."""

    band: DifficultyBand
    difficulty: float
    steps: tuple[ComputeStep, ...]
    max_parallel_agents: int
    max_review_rounds: int
    independent_approaches: int
    rationale: str

    def step_names(self) -> list[str]:
        return [step.name for step in self.steps if step.required]

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band.value,
            "difficulty": round(self.difficulty, 6),
            "steps": [step.to_dict() for step in self.steps],
            "required_steps": self.step_names(),
            "max_parallel_agents": self.max_parallel_agents,
            "max_review_rounds": self.max_review_rounds,
            "independent_approaches": self.independent_approaches,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class DifficultyEstimate:
    """A normalised difficulty plus its full evidence."""

    difficulty: float
    band: DifficultyBand
    coverage: float
    """Fraction of configured signal weight that was actually measured, 0..1."""

    measured: dict[str, float] = field(default_factory=dict)
    unmeasured: list[str] = field(default_factory=list)
    plan: ComputePlan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty": round(self.difficulty, 6),
            "band": self.band.value,
            "coverage": round(self.coverage, 4),
            "measured": {k: round(v, 6) for k, v in self.measured.items()},
            "unmeasured": list(self.unmeasured),
            "plan": self.plan.to_dict() if self.plan else None,
        }


class DifficultyEstimator:
    """Estimates difficulty and allocates bounded compute."""

    def __init__(
        self,
        *,
        weights: Mapping[str, float] | None = None,
        bands: Mapping[str, float] | None = None,
    ) -> None:
        self.weights = dict(weights or DEFAULT_SIGNAL_WEIGHTS)
        bands = dict(bands or DEFAULT_BANDS)
        for band in DifficultyBand:
            if band is DifficultyBand.VERY_HARD:
                continue
            value = bands.get(band.value)
            if value is None or not 0.0 < value < 1.0:
                raise ValueError(f"band boundary for {band.value!r} must be within (0.0, 1.0), got {value!r}")
        self.bands = bands

    def band_for(self, difficulty: float) -> DifficultyBand:
        if difficulty < self.bands[DifficultyBand.EASY.value]:
            return DifficultyBand.EASY
        if difficulty < self.bands[DifficultyBand.MEDIUM.value]:
            return DifficultyBand.MEDIUM
        if difficulty < self.bands[DifficultyBand.HARD.value]:
            return DifficultyBand.HARD
        return DifficultyBand.VERY_HARD

    def estimate(self, signals: DifficultySignals, *, with_plan: bool = True) -> DifficultyEstimate:
        """Combine measured signals into a 0..1 difficulty score.

        With nothing measured the score is ``0.0`` and coverage ``0.0`` — an
        unmeasured task is not an easy task, and a caller that treats the score
        as a measurement should be able to see it is not one.
        """
        measured = signals.measured()
        if not measured:
            estimate = DifficultyEstimate(
                difficulty=0.0,
                band=self.band_for(0.0),
                coverage=0.0,
                measured={},
                unmeasured=sorted(self.weights),
            )
            if with_plan:
                estimate = DifficultyEstimate(
                    difficulty=estimate.difficulty,
                    band=estimate.band,
                    coverage=0.0,
                    measured={},
                    unmeasured=estimate.unmeasured,
                    plan=self.plan_for(estimate),
                )
            return estimate

        total_weight = sum(self.weights.get(name, 0.0) for name in measured)
        if total_weight <= 0:
            difficulty = 0.0
            coverage = 0.0
        else:
            difficulty = sum(measured[name] * self.weights.get(name, 0.0) for name in measured) / total_weight
            coverage = total_weight / sum(self.weights.values()) if sum(self.weights.values()) else 0.0
        difficulty = max(0.0, min(1.0, difficulty))
        estimate = DifficultyEstimate(
            difficulty=difficulty,
            band=self.band_for(difficulty),
            coverage=coverage,
            measured=measured,
            unmeasured=sorted(name for name in self.weights if name not in measured),
        )
        if with_plan:
            estimate = DifficultyEstimate(
                difficulty=estimate.difficulty,
                band=estimate.band,
                coverage=estimate.coverage,
                measured=estimate.measured,
                unmeasured=estimate.unmeasured,
                plan=self.plan_for(estimate),
            )
        return estimate

    def plan_for(self, estimate: DifficultyEstimate) -> ComputePlan:
        """Allocate compute for ``estimate``. Every value is a hard ceiling.

        The bands follow the prompt directly, and the *point* of the module is
        that the ceiling is low for easy work: never spending maximum compute on
        every task is the entire reason this exists.
        """
        band = estimate.band
        plan = _PLANS[band]
        rationale = f"{band.value} (difficulty {estimate.difficulty:.2f}, coverage {estimate.coverage:.0%} of signal weight measured)"
        return ComputePlan(
            band=band,
            difficulty=estimate.difficulty,
            steps=plan["steps"],
            max_parallel_agents=plan["agents"],
            max_review_rounds=plan["reviews"],
            independent_approaches=plan["approaches"],
            rationale=rationale,
        )


_PLANS: dict[DifficultyBand, dict[str, Any]] = {
    DifficultyBand.EASY: {
        "steps": (ComputeStep("plan", True, "one planning pass", "max_recursion_limit"),),
        "agents": 1,
        "reviews": 0,
        "approaches": 1,
    },
    DifficultyBand.MEDIUM: {
        "steps": (
            ComputeStep("plan", True, "planning", "max_recursion_limit"),
            ComputeStep("execute", True, "single execution pass", "token_budget"),
            ComputeStep("verify", True, "verify the result", "verification.max_rounds"),
        ),
        "agents": 1,
        "reviews": 1,
        "approaches": 1,
    },
    DifficultyBand.HARD: {
        "steps": (
            ComputeStep("plan", True, "planning", "max_recursion_limit"),
            ComputeStep("execute", True, "execution", "token_budget"),
            ComputeStep("test", True, "run the relevant tests", "verification.timeout"),
            ComputeStep("review", True, "independent review pass", "verification.max_rounds"),
        ),
        "agents": 3,
        "reviews": 2,
        "approaches": 1,
    },
    DifficultyBand.VERY_HARD: {
        "steps": (
            ComputeStep("plan", True, "deep planning", "max_recursion_limit"),
            ComputeStep("execute", True, "execution", "token_budget"),
            ComputeStep("test", True, "run the relevant tests", "verification.timeout"),
            ComputeStep("review", True, "review pass", "verification.max_rounds"),
            ComputeStep("cross_check", True, "second, independent approach", "subagents.max_total_per_run"),
            ComputeStep("verify", True, "final verification", "verification.max_rounds"),
        ),
        "agents": 5,
        "reviews": 3,
        "approaches": 2,
    },
}
