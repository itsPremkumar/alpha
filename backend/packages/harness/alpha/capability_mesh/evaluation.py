"""Evaluation harness, dynamic rubric selection and diversity exploration.

Plan §16 (Feature O) wants the *routing* evaluated rather than the answer:
assignment fit, contribution quality, marginal/complementarity value, calibration,
routing regret, resource effectiveness, robustness and explainability. Plan §19
(Feature R) wants the rubric chosen from the microtask type instead of one
general-purpose average, and plan §17 (Feature P) wants exploration that does not
waste every request. This module is those three ideas reduced to data and pure
functions — there is still **no executor wiring**: nothing here runs a case, calls
a model, or dispatches work.

Five rules decide the shape of this module:

* **A rubric is selected from a table, never inferred.** :func:`select_rubric`
  is a deterministic lookup by microtask kind. A kind with no rubric is an honest
  refusal naming what *was* available, because silently borrowing the nearest
  rubric would measure the wrong dimension and report it as if it were right.
* **Two versions of one rubric id is a refusal, not a pick.** The comparability
  rule: a score measured under ``r1`` is not comparable with one measured under
  ``r2``, so two rubrics claiming the same id under different versions is a
  configuration error this function names rather than resolves by dictionary
  order.
* **The holdout is separate because §13.2 says so.** An
  :class:`EvaluationSuite` refuses any holdout ``case_id`` that also appears in
  its scored ``cases``: a holdout that shares cases with the scored set does not
  hold anything out, it just lets the router be tuned to its own answers.
* **A report is per CATEGORY and never a leaderboard.** Plan §4.1: "move beyond
  a leaderboard". :class:`EvaluationReport` refuses ``global_rank_available=True``
  and carries no global-rank field at all — an averaged single number over
  heterogeneous task categories is exactly the artifact the plan forbids.
  ``routing_regret`` is ``None`` without a candidate set (and ``None`` when no
  case is comparable), and ``cost_at_quality`` is ``None`` because
  :func:`evaluate` receives no cost data whatsoever — never a fabricated 0.0.
* **Exploration is gated, and "I know nothing" is not a reason to spend.** A
  zero exploration share is honoured (plan §17's "no experimentation on my
  requests" setting), an unknown or exhausted budget refuses, and when neither
  side has a measured score :func:`should_explore` returns ``False`` with a
  reason rather than burning a request to learn nothing.

The limits are real and stated: the scores here are caller-supplied numbers, not
measurements this module takes; ``THIN_INCUMBENT_FLOOR`` is a documented starting
policy (plan §16.2 forbids presenting a percentage improvement from a tiny
sample as universal fact); and :func:`should_explore` receives no sample count,
so "thin evidence" is read from the score alone — a one-sample 0.9 looks
established here, and a later layer must carry the sample count to fix that.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The score below which the incumbent is treated as NOT well established, so
#: the exploration share is allowed to test it. A documented starting policy to
#: be tuned against measured routing outcomes — never presented as a measured
#: threshold.
THIN_INCUMBENT_FLOOR = 0.6

#: Separator between a scored ``case_id`` and a ``case_id::entity_id`` benchmark
#: key. A key is only read as a benchmark key when it is NOT a bare ``case_id``
#: the suite knows, so a case id that happens to contain the separator is never
#: misparsed.
BENCHMARK_KEY_SEPARATOR = "::"


def _non_empty(value: Any, field_name: str) -> str:
    """Coerce to ``str`` and refuse empty, naming the field."""
    text = str(value)
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _unit_score(value: Any, field_name: str, *, nullable: bool = False) -> float:
    """Coerce to a finite float in [0, 1]; refuse anything else by name."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{field_name} must be a finite number in [0, 1]{' or None' if nullable else ''}, got {value!r}")
    return float(value)


def _optional_unit_score(value: Any, field_name: str) -> float | None:
    """``None`` stays ``None``; anything present must be a finite number in [0, 1]."""
    if value is None:
        return None
    return _unit_score(value, field_name, nullable=True)


def _non_negative(value: Any, field_name: str, *, nullable: bool = False) -> float:
    """Coerce to a finite float >= 0 (a cost), refusing anything else by name."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0.0:
        raise ValueError(f"{field_name} must be a finite number >= 0{' or None' if nullable else ''}, got {value!r}")
    return float(value)


# -------------------------------------------------------------------- rubric


@dataclass(frozen=True)
class Rubric:
    """One rubric for one class of microtask (plan §19, Feature R).

    ``rubric_version`` is REQUIRED and travels with every score measured under
    it: a rubric change makes older scores attributable rather than
    comparable-by-accident, which is why :func:`select_rubric` refuses two
    versions of one id instead of picking one.

    ``criteria`` is REQUIRED non-empty — a rubric with no criteria scores
    nothing. ``dimension_weights`` are RELATIVE weights (this module never
    normalises them and never computes a combined score from them); an empty
    tuple means "no weighted dimensions declared, gates only", while a
    non-empty tuple summing to zero is refused because it cannot discriminate.
    ``hard_gates`` are the non-negotiable conditions of §19 ("do not let a
    single weighted average mask fatal failures") — a gate is a gate, never a
    penalty, so it is a list of conditions and nothing else.
    """

    rubric_id: str
    rubric_version: str
    criteria: tuple[str, ...]
    dimension_weights: tuple[tuple[str, float], ...] = ()
    hard_gates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rubric_id", _non_empty(self.rubric_id, "rubric_id"))
        object.__setattr__(self, "rubric_version", _non_empty(self.rubric_version, "rubric_version"))

        criteria = tuple(self.criteria)
        if not criteria:
            raise ValueError("criteria must be non-empty: a rubric with no criteria scores nothing")
        for entry in criteria:
            _non_empty(entry, "criteria entries")
        object.__setattr__(self, "criteria", criteria)

        weights: list[tuple[str, float]] = []
        seen: set[str] = set()
        for entry in tuple(self.dimension_weights):
            pair = tuple(entry)
            if len(pair) != 2:
                raise ValueError(f"dimension_weights entries must be (dimension, weight) pairs, got {entry!r}")
            name, weight = _non_empty(pair[0], "dimension_weights entries' dimension name"), _non_negative(pair[1], f"dimension_weights weight for '{pair[0]}'")
            if name in seen:
                raise ValueError(f"duplicate rubric dimension '{name}': one weight per dimension, or the rubric double-counts it")
            seen.add(name)
            weights.append((name, weight))
        if weights and sum(weight for _, weight in weights) <= 0.0:
            raise ValueError("dimension_weights weights sum to 0.0: a rubric that weights nothing cannot discriminate between candidates")
        object.__setattr__(self, "dimension_weights", tuple(weights))

        gates = tuple(self.hard_gates)
        for gate in gates:
            _non_empty(gate, "hard_gates entries")
        object.__setattr__(self, "hard_gates", gates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric_id": self.rubric_id,
            "rubric_version": self.rubric_version,
            "criteria": list(self.criteria),
            "dimension_weights": [[name, weight] for name, weight in self.dimension_weights],
            "hard_gates": list(self.hard_gates),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rubric:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            rubric_id=str(data["rubric_id"]),
            rubric_version=str(data["rubric_version"]),
            criteria=tuple(str(entry) for entry in data["criteria"]),
            dimension_weights=tuple((str(name), float(weight)) for name, weight in data.get("dimension_weights", ())),
            hard_gates=tuple(str(gate) for gate in data.get("hard_gates", ())),
        )


@dataclass(frozen=True)
class RubricSelection:
    """The rubric chosen for one microtask kind, with the reason and version.

    ``profile_version`` is the selected rubric's own ``rubric_version``: it rides
    along so a score measured under this rubric is always traceable to the
    rubric that produced it (the Phase B comparability rule). ``reason`` is
    REQUIRED non-empty — a selection that cannot say why it picked this rubric
    is indistinguishable from one that picked at random.
    """

    rubric: Rubric
    reason: str
    profile_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.rubric, Rubric):
            raise ValueError(f"rubric must be a Rubric, got {self.rubric!r}")
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))
        object.__setattr__(self, "profile_version", _non_empty(self.profile_version, "profile_version"))

    def to_dict(self) -> dict[str, Any]:
        return {"rubric": self.rubric.to_dict(), "reason": self.reason, "profile_version": self.profile_version}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricSelection:
        return cls(rubric=Rubric.from_dict(data["rubric"]), reason=str(data["reason"]), profile_version=str(data["profile_version"]))


def select_rubric(*, microtask_kind: str, available: Mapping[str, Rubric]) -> RubricSelection:
    """Pick the rubric registered for ``microtask_kind`` — a table lookup, not inference.

    Deterministic: the same kind and the same ``available`` always yield the same
    selection (plan §4: "router output is deterministic given identical input").

    Two refusals, both by name:

    * no rubric for the kind — the message lists every kind that *was*
      available, because a miss that only says "not found" leaves the caller
      guessing whether the table is empty or the key is wrong;
    * two rubrics sharing one ``rubric_id`` under different ``rubric_version``s
      — a comparability failure. This is checked across the WHOLE ``available``
      set, not just the selected row, because two versions in one table is a
      configuration error that makes any score from it incomparable by accident.
    """
    kind = _non_empty(microtask_kind, "microtask_kind")
    rubrics = dict(available)
    for registered_kind, rubric in rubrics.items():
        _non_empty(registered_kind, "available keys (microtask kinds)")
        if not isinstance(rubric, Rubric):
            raise ValueError(f"available[{registered_kind!r}] must be Rubric rows, got {rubric!r}")

    versions_by_id: dict[str, set[str]] = {}
    for rubric in rubrics.values():
        versions_by_id.setdefault(rubric.rubric_id, set()).add(rubric.rubric_version)
    conflicted = {rubric_id: sorted(versions) for rubric_id, versions in versions_by_id.items() if len(versions) > 1}
    if conflicted:
        rubric_id, versions = sorted(conflicted.items())[0]
        raise ValueError(f"rubric id '{rubric_id}' is registered under more than one version ({', '.join(versions)}): scores measured under different rubric versions are not comparable, so no rubric is selected")

    match = rubrics.get(kind)
    if match is None:
        offered = ", ".join(sorted(rubrics)) if rubrics else "(none)"
        raise ValueError(f"no rubric is registered for microtask kind '{kind}'; available kinds: {offered}")

    return RubricSelection(
        rubric=match,
        reason=f"microtask kind '{kind}' resolved to rubric '{match.rubric_id}' v{match.rubric_version} by direct table lookup (deterministic; no inference and no fallback rubric)",
        profile_version=match.rubric_version,
    )


# ---------------------------------------------------------------------- case


@dataclass(frozen=True)
class EvaluationCase:
    """One benchmark case: what it asks, what capability it exercises, what must hold.

    ``expected_checks`` is REQUIRED non-empty: a case nobody can accept or
    reject cannot evaluate routing, it can only produce a number. ``tags`` is
    free-form grouping metadata and carries no honesty weight.
    """

    case_id: str
    task_category: str
    capability: str
    expected_checks: tuple[str, ...]
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("case_id", "task_category", "capability"):
            object.__setattr__(self, field_name, _non_empty(getattr(self, field_name), field_name))
        checks = tuple(self.expected_checks)
        if not checks:
            raise ValueError("expected_checks must be non-empty: a case with nothing to check cannot evaluate a routing decision")
        for entry in checks:
            _non_empty(entry, "expected_checks entries")
        object.__setattr__(self, "expected_checks", checks)
        tags = tuple(self.tags)
        for entry in tags:
            _non_empty(entry, "tags entries")
        object.__setattr__(self, "tags", tags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "task_category": self.task_category,
            "capability": self.capability,
            "expected_checks": list(self.expected_checks),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationCase:
        return cls(
            case_id=str(data["case_id"]),
            task_category=str(data["task_category"]),
            capability=str(data["capability"]),
            expected_checks=tuple(str(entry) for entry in data["expected_checks"]),
            tags=tuple(str(tag) for tag in data.get("tags", ())),
        )


@dataclass(frozen=True)
class EvaluationSuite:
    """A versioned benchmark suite plus its SEPARATE holdout (plan §13.2).

    ``cases`` is REQUIRED non-empty. ``holdout`` may be empty, but a holdout
    ``case_id`` that also appears in ``cases`` is refused by name: plan §13.2
    keeps the holdout apart specifically "to prevent tuning the router to its own
    test answers", and an overlapping holdout quietly removes that protection
    while still looking like a holdout exists.
    """

    suite_id: str
    version: str
    cases: tuple[EvaluationCase, ...]
    holdout: tuple[EvaluationCase, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_id", _non_empty(self.suite_id, "suite_id"))
        object.__setattr__(self, "version", _non_empty(self.version, "version"))
        cases = tuple(self.cases)
        if not cases:
            raise ValueError("cases must be non-empty: an evaluation suite with no cases evaluates nothing")
        holdout = tuple(self.holdout)
        for label, rows in (("cases", cases), ("holdout", holdout)):
            for row in rows:
                if not isinstance(row, EvaluationCase):
                    raise ValueError(f"{label} must contain EvaluationCase rows, got {row!r}")
            seen: set[str] = set()
            for row in rows:
                if row.case_id in seen:
                    raise ValueError(f"duplicate case_id '{row.case_id}' in {label}: one case, one id")
                seen.add(row.case_id)
        scored_ids = {case.case_id for case in cases}
        overlap = sorted(scored_ids & {case.case_id for case in holdout})
        if overlap:
            raise ValueError(f"holdout case_id(s) {', '.join(overlap)} also appear in cases: a holdout that shares cases with the scored set holds nothing out (plan §13.2: it exists so the router is not tuned to its own answers)")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "holdout", holdout)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "version": self.version,
            "cases": [case.to_dict() for case in self.cases],
            "holdout": [case.to_dict() for case in self.holdout],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationSuite:
        return cls(
            suite_id=str(data["suite_id"]),
            version=str(data["version"]),
            cases=tuple(EvaluationCase.from_dict(entry) for entry in data["cases"]),
            holdout=tuple(EvaluationCase.from_dict(entry) for entry in data.get("holdout", ())),
        )


# --------------------------------------------------------------------- report


@dataclass(frozen=True)
class EvaluationReport:
    """What one suite run measured, per task category. Not a leaderboard.

    **No single global rank is produced, and none can be requested**:
    ``global_rank_available`` exists only to state that, and setting it ``True``
    is refused by name — plan §4.1 ("move beyond a leaderboard") and §16.2
    (never report a percentage improvement from a tiny unrepresentative sample
    as a universal fact). An entity that dominates one category is not thereby
    the best executor for another, so the rows stay per category.

    ``routing_regret`` is the mean gap between the routed run and the best
    observed eligible candidate over the cases where BOTH numbers were supplied
    (plan §16.1's "how far was the result from the best observed eligible
    candidate"), so it is ``None`` when no candidate set was supplied and
    ``None`` when no case was comparable — never 0.0 over nothing.
    ``cost_at_quality`` is ``None`` whenever nothing was priced, and
    :func:`evaluate` always reports it ``None`` because it is handed no cost data
    at all. ``sample_count`` counts the outcomes the rows were computed from; it
    may not be 0 beside rows, nor positive beside none.
    """

    suite_id: str
    per_category: tuple[tuple[str, float], ...]
    global_rank_available: bool = False
    routing_regret: float | None = None
    cost_at_quality: float | None = None
    sample_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_id", _non_empty(self.suite_id, "suite_id"))
        rows: list[tuple[str, float]] = []
        seen: set[str] = set()
        for entry in tuple(self.per_category):
            pair = tuple(entry)
            if len(pair) != 2:
                raise ValueError(f"per_category entries must be (task_category, score) pairs, got {entry!r}")
            category = _non_empty(pair[0], "per_category task category")
            if category in seen:
                raise ValueError(f"duplicate category '{category}' in per_category: one score per category, or the report double-counts it")
            seen.add(category)
            rows.append((category, _unit_score(pair[1], f"per_category score for '{category}'")))
        object.__setattr__(self, "per_category", tuple(sorted(rows)))
        if self.global_rank_available is not False:
            raise ValueError(
                f"global_rank_available must be False (got {self.global_rank_available!r}): this report produces no single global rank, "
                "because plan §4.1 moves beyond a leaderboard — a global average over heterogeneous task categories hides the fit the router exists to find"
            )
        object.__setattr__(self, "routing_regret", _optional_unit_score(self.routing_regret, "routing_regret"))
        object.__setattr__(self, "cost_at_quality", None if self.cost_at_quality is None else _non_negative(self.cost_at_quality, "cost_at_quality", nullable=True))
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError(f"sample_count must be an int >= 0, got {self.sample_count!r}")
        if self.per_category and self.sample_count < 1:
            raise ValueError(f"sample_count must be >= 1 when per_category carries rows ({len(self.per_category)} row(s)), got {self.sample_count}")
        if not self.per_category and self.sample_count:
            raise ValueError(f"sample_count must be 0 when per_category carries no rows, got {self.sample_count}: a count beside no scores is a measurement nobody took")

    def score_for(self, task_category: str) -> float | None:
        """The measured score for one category, or ``None`` — never a guess."""
        wanted = _non_empty(task_category, "task_category")
        for category, score in self.per_category:
            if category == wanted:
                return score
        return None

    def to_dict(self) -> dict[str, Any]:
        # Deliberately no global-rank key: the field exists only to be False.
        return {
            "suite_id": self.suite_id,
            "per_category": [[category, score] for category, score in self.per_category],
            "global_rank_available": self.global_rank_available,
            "routing_regret": self.routing_regret,
            "cost_at_quality": self.cost_at_quality,
            "sample_count": self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationReport:
        return cls(
            suite_id=str(data["suite_id"]),
            per_category=tuple((str(category), float(score)) for category, score in data.get("per_category", ())),
            global_rank_available=bool(data.get("global_rank_available", False)),
            routing_regret=None if data.get("routing_regret") is None else float(data["routing_regret"]),
            cost_at_quality=None if data.get("cost_at_quality") is None else float(data["cost_at_quality"]),
            sample_count=int(data.get("sample_count", 0)),
        )


def _split_benchmark_key(key: str) -> tuple[str, str] | None:
    """Split ``"<case_id>::<entity_id>"``, or return None when it is not one."""
    case_id, separator, entity_id = key.partition(BENCHMARK_KEY_SEPARATOR)
    if not separator or not case_id or not entity_id:
        return None
    return case_id, entity_id


def evaluate(
    suite: EvaluationSuite,
    outcomes: Mapping[str, float],
    *,
    candidates: Mapping[str, str] | None = None,
    chosen: str | None = None,
) -> EvaluationReport:
    """Score one suite run per task category, and refuse anything unmeasured.

    ``outcomes`` maps a suite ``case_id`` to the quality measured for the run
    under evaluation (in [0, 1]). A key the suite does not know is refused by
    name — including a **holdout** case id, which must be scored in a separate
    call against a suite that names it (plan §13.2).

    ``candidates`` maps a case id to the best observed eligible candidate's
    entity id for that case, and ``chosen`` names the entity the router
    actually picked. Routing regret then compares the two, but only where the
    alternative's own measurement was supplied: pass it as an outcome keyed
    ``"<case_id>::<entity_id>"``. A case with no benchmark row contributes
    nothing, so a candidate set that measures nothing yields ``None`` — the
    module never assumes an unmeasured candidate would have scored worse.

    ``cost_at_quality`` is always ``None``: this function is handed no cost,
    latency or token data, and inventing a price would be the one number a
    reader would trust most. ``chosen`` is recorded for the caller's own
    attribution — a named choice with no measured alternative is still a
    ``None`` regret, never an implied 0.0.
    """
    if not isinstance(suite, EvaluationSuite):
        raise ValueError(f"evaluate() expects an EvaluationSuite, got {suite!r}")
    known_ids = {case.case_id for case in suite.cases}
    holdout_ids = {case.case_id for case in suite.holdout}

    scored: dict[str, float] = {}
    benchmark: dict[tuple[str, str], float] = {}
    for key, raw in outcomes.items():
        # A bare case id is checked first, so a case id containing the
        # separator is still read as a case id.
        if key in known_ids:
            scored[key] = _unit_score(raw, f"outcome '{key}'")
            continue
        if key in holdout_ids:
            raise ValueError(
                f"outcome '{key}' names a HOLDOUT case of suite '{suite.suite_id}' ({suite.version}): plan §13.2 keeps the holdout separate "
                "so the router is never tuned to its own answers — score it in a separate call whose suite names it as a scored case"
            )
        split = _split_benchmark_key(key)
        if split is None or split[0] not in known_ids:
            raise ValueError(f"outcome '{key}' is not a case id in suite '{suite.suite_id}' ({suite.version}): known cases are {', '.join(sorted(known_ids))}")
        benchmark[split] = _unit_score(raw, f"benchmark outcome '{key}'")

    totals: dict[str, list[float]] = {}
    for case in suite.cases:
        score = scored.get(case.case_id)
        if score is not None:
            totals.setdefault(case.task_category, []).append(score)
    per_category = tuple((category, sum(scores) / len(scores)) for category, scores in totals.items())

    regret: float | None = None
    if candidates:
        if chosen is not None:
            _non_empty(chosen, "chosen")
        gaps: list[float] = []
        for case_id, best_entity in candidates.items():
            if case_id not in known_ids:
                raise ValueError(f"candidates names '{case_id}', which is not a case id in suite '{suite.suite_id}' ({suite.version})")
            if case_id not in scored:
                continue
            best_score = benchmark.get((case_id, _non_empty(best_entity, f"candidates[{case_id!r}]")))
            if best_score is None:
                continue
            gaps.append(max(0.0, best_score - scored[case_id]))
        if gaps:
            regret = sum(gaps) / len(gaps)

    return EvaluationReport(
        suite_id=suite.suite_id,
        per_category=per_category,
        routing_regret=regret,
        cost_at_quality=None,
        sample_count=len(scored),
    )


# ---------------------------------------------------------------- exploration


@dataclass(frozen=True)
class ExplorationDecision:
    """Whether to spend one request probing an under-measured candidate, and why.

    ``explore=False`` is a decision, not a failure, so ``reason`` is REQUIRED
    non-empty in both directions: "no exploration" with no sentence is
    indistinguishable from a bug. ``remaining_budget_fraction`` echoes the
    budget the decision was taken against (``None`` when it was unknown).
    """

    explore: bool
    reason: str
    remaining_budget_fraction: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.explore, bool):
            raise ValueError(f"explore must be a bool, got {self.explore!r}")
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))
        object.__setattr__(self, "remaining_budget_fraction", _optional_unit_score(self.remaining_budget_fraction, "remaining_budget_fraction"))

    def to_dict(self) -> dict[str, Any]:
        return {"explore": self.explore, "reason": self.reason, "remaining_budget_fraction": self.remaining_budget_fraction}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplorationDecision:
        return cls(explore=bool(data["explore"]), reason=str(data["reason"]), remaining_budget_fraction=data.get("remaining_budget_fraction"))


def should_explore(
    *,
    incumbent_score: float | None,
    challenger_score: float | None,
    remaining_budget_fraction: float | None,
    exploration_budget_fraction: float = 0.1,
) -> ExplorationDecision:
    """Decide whether one request may test an under-measured candidate (plan §17).

    Exploration happens only when BOTH hold:

    * **the exploration share has budget** — a ``exploration_budget_fraction``
      of 0.0 is the user's "no experimentation on my requests" setting and is
      honoured; and
    * **the incumbent's evidence is thin** — unmeasured, or measured below
      :data:`THIN_INCUMBENT_FLOOR`.

    Two honest refusals come first: an unknown or exhausted remaining budget
    (spending a request nobody can account for is not exploration, it is
    leakage), and **nothing measured on either side** — when both scores are
    ``None`` there is no alternative being tested and no evidence being
    gathered, so spending the request would buy nothing but cost.

    The bounds on both fractions are REFUSED, never clamped: a share silently
    turned into 1.0 would explore on every request.
    """
    share = _unit_score(exploration_budget_fraction, "exploration_budget_fraction")
    remaining = _optional_unit_score(remaining_budget_fraction, "remaining_budget_fraction")
    incumbent = _optional_unit_score(incumbent_score, "incumbent_score")
    challenger = _optional_unit_score(challenger_score, "challenger_score")

    def decide(explore: bool, reason: str, *, budget: float | None = remaining) -> ExplorationDecision:
        return ExplorationDecision(explore=explore, reason=reason, remaining_budget_fraction=budget)

    rival = "unmeasured" if challenger is None else f"{challenger:.2f}"

    if share <= 0.0:
        return decide(False, "exploration budget share is 0.0: exploration is opt-in (plan §17), so the under-measured incumbent is exploited as-is")
    if remaining is None:
        return decide(False, "remaining budget is unknown: exploration may not spend a request it cannot account for", budget=None)
    if remaining <= 0.0:
        return decide(False, "no budget remains: there is nothing left to explore with")
    if incumbent is None and challenger is None:
        return decide(False, f"nothing measured: neither the incumbent nor the challenger has a score, so this request would buy no evidence (exploration share {share} is available)")
    if incumbent is None:
        return decide(True, f"the incumbent is unmeasured while the challenger measures {rival}: an unmeasured incumbent is the thinnest evidence there is, and the exploration share {share} is available")
    if incumbent < THIN_INCUMBENT_FLOOR:
        return decide(True, f"incumbent score {incumbent:.2f} is below the thin-evidence floor {THIN_INCUMBENT_FLOOR}: a weak leader is worth testing against the challenger ({rival}), and the exploration share {share} is available")
    return decide(False, f"incumbent score {incumbent:.2f} is at or above the thin-evidence floor {THIN_INCUMBENT_FLOOR}: the leader is established, so an exploration request would spend budget on a second opinion nobody asked for")


__all__ = [
    "BENCHMARK_KEY_SEPARATOR",
    "THIN_INCUMBENT_FLOOR",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationSuite",
    "ExplorationDecision",
    "Rubric",
    "RubricSelection",
    "evaluate",
    "select_rubric",
    "should_explore",
]
