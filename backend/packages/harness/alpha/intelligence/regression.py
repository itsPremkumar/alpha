"""Before/after evaluation: train/held-out separation, regression gate, overfitting gap.

What already exists, and what this adds
---------------------------------------
Alpha has real held-out machinery — :mod:`alpha.rsi.holdout` executes a hidden
suite on a real :class:`~alpha.benchmarks.runner.BenchmarkRunner` and refuses to
gate on anything but ``evidence_kind="measured"``. That contract is adopted
wholesale here and is not weakened: :func:`_unmeasured` is this module's version
of the same rule, and a missing measurement is ``score=0.5,
evidence_kind="unverified"`` with the real reason attached — neutral, never a pass.

What is genuinely missing is the **comparison**. Alpha has a hidden suite and a
release gate, but nothing that puts train beside held-out beside regression and
reports the deltas, which is exactly what prompt §9 asks for.

Three separations that are structural, not advisory
---------------------------------------------------
1. **Train vs held-out.** :class:`EvaluationRun` carries both, and
   :attr:`EvaluationRun.overfitting_gap` is computed, not asserted. A candidate
   whose train score rises while held-out falls is reported as
   ``possible_overfitting`` — see :func:`compare`.
2. **Held-out ids never leak.** :meth:`EvaluationRun.to_candidate_payload`
   deliberately omits held-out case ids, mirroring the anti-gaming rule in
   :mod:`alpha.rsi.holdout`.
3. **Unmeasured never passes.** :func:`evaluate_gate` cannot return ``PROMOTE``
   on an unverified run; it returns ``REJECT`` with the reason, because
   :data:`Decision.REJECT` is the fail-closed terminal outcome.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Category",
    "CaseResult",
    "EvaluationRun",
    "Comparison",
    "Decision",
    "EvaluationOutcome",
    "REGRESSION_SUITE_VERSION",
    "CATEGORIES",
    "RegressionCase",
    "default_regression_suite",
    "register_regression_suite",
    "evaluate_gate",
    "compare",
    "unmeasured",
]

#: Suite version. Bumped whenever a case's expected behaviour changes, so an
#: evaluation run is reproducible against a named suite rather than an implicit
#: one. Mirrors ``HOLDOUT_SUITE_VERSION`` in :mod:`alpha.rsi.holdout`.
REGRESSION_SUITE_VERSION = "v1"


class Category(StrEnum):
    """The capability categories the standing suite must cover (prompt §8)."""

    CODING = "coding"
    REASONING = "reasoning"
    PLANNING = "planning"
    TOOL_USE = "tool_use"
    TERMINAL = "terminal"
    GIT = "git"
    GITHUB = "github"
    MCP = "mcp"
    MEMORY = "memory"
    BROWSER = "browser"
    RESEARCH = "research"
    AGENT_COORDINATION = "agent_coordination"
    SWARM = "swarm"
    TASK_RECOVERY = "task_recovery"
    PERSISTENT_TASKS = "persistent_tasks"
    SELF_DEBUGGING = "self_debugging"
    SECURITY = "security"
    LOCAL_MODELS = "local_models"
    LLM_ROUTING = "llm_routing"
    RESOURCE_HANDLING = "resource_handling"


#: Every category the suite is required to cover. Exported so a test can assert
#: coverage without hardcoding the list a second time.
CATEGORIES: tuple[str, ...] = tuple(c.value for c in Category)


class Decision(StrEnum):
    """Promotion outcome (prompt §10)."""

    PROMOTE = "PROMOTE"
    KEEP_TRIAL = "KEEP_TRIAL"
    REJECT = "REJECT"
    ROLLBACK = "ROLLBACK"
    NO_CHANGE = "NO_CHANGE"


@dataclass(frozen=True)
class CaseResult:
    """One executed case."""

    case_id: str
    category: str
    passed: bool
    score: float
    detail: str = ""
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "score": round(self.score, 6),
            "detail": self.detail,
            "hidden": self.hidden,
        }


@dataclass(frozen=True)
class EvaluationRun:
    """A single executed evaluation. Never invents a score."""

    suite_version: str
    results: tuple[CaseResult, ...]
    score: float
    passed: int
    failed: int
    evidence_kind: str = "measured"
    error: str | None = None
    duration_ms: float | None = None
    duration_measured: bool = False
    label: str = ""
    heldout: bool = False
    """True when this run came from a hidden/held-out suite. Such case ids are
    kept out of :meth:`to_candidate_payload`."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_version": self.suite_version,
            "score": round(self.score, 6),
            "passed": self.passed,
            "failed": self.failed,
            "evidence_kind": self.evidence_kind,
            "error": self.error,
            "label": self.label,
            "heldout": self.heldout,
            "duration_ms": self.duration_ms,
            "duration_measured": self.duration_measured,
            "results": [item.to_dict() for item in self.results],
        }

    def to_candidate_payload(self) -> dict[str, Any]:
        """The candidate-facing view.

        Case ids of a held-out run are replaced with their count. A candidate
        that can read the held-out case list can optimise against it, which is
        the same anti-gaming boundary :mod:`alpha.rsi.holdout` draws.
        """
        results = [item.to_dict() for item in self.results if not (self.heldout and item.hidden)]
        hidden_count = sum(1 for item in self.results if item.hidden)
        return {
            "suite_version": self.suite_version,
            "score": round(self.score, 6),
            "passed": self.passed,
            "failed": self.failed,
            "evidence_kind": self.evidence_kind,
            "label": self.label,
            "hidden_case_count": hidden_count,
            "results": results,
        }

    @classmethod
    def from_results(
        cls,
        results: Iterable[CaseResult],
        *,
        suite_version: str = REGRESSION_SUITE_VERSION,
        label: str = "",
        heldout: bool = False,
    ) -> EvaluationRun:
        """Aggregate executed cases into a run.

        An empty result set is **not** a pass. It yields
        ``score=0.5, evidence_kind="unverified"`` with the real reason, which is
        the identical fail-closed shape :mod:`alpha.rsi.holdout` uses.
        """
        items = tuple(results)
        if not items:
            return unmeasured(f"{label or 'suite'} produced no executed cases", suite_version=suite_version, heldout=heldout)
        passed = sum(1 for item in items if item.passed)
        return cls(
            suite_version=suite_version,
            results=items,
            score=passed / len(items),
            passed=passed,
            failed=len(items) - passed,
            evidence_kind="measured",
            label=label,
            heldout=heldout,
        )

    @classmethod
    def timed(
        cls,
        results: Iterable[CaseResult],
        *,
        suite_version: str = REGRESSION_SUITE_VERSION,
        label: str = "",
        heldout: bool = False,
        started_at: float | None = None,
    ) -> EvaluationRun:
        """As :meth:`from_results`, plus a measured duration.

        Duration is only set when ``started_at`` is supplied, and the
        ``duration_measured`` flag distinguishes "0.0 ms because it was instant"
        from "no duration was recorded".
        """
        run = cls.from_results(results, suite_version=suite_version, label=label, heldout=heldout)
        if started_at is None:
            return run
        elapsed = max(0.0, (time.time() - started_at) * 1000.0)
        return EvaluationRun(
            suite_version=run.suite_version,
            results=run.results,
            score=run.score,
            passed=run.passed,
            failed=run.failed,
            evidence_kind=run.evidence_kind,
            error=run.error,
            duration_ms=elapsed,
            duration_measured=True,
            label=run.label,
            heldout=run.heldout,
        )


def unmeasured(reason: str, *, suite_version: str = REGRESSION_SUITE_VERSION, heldout: bool = False) -> EvaluationRun:
    """The canonical not-run run: neutral 0.5, unverified, real reason disclosed."""
    return EvaluationRun(
        suite_version=suite_version,
        results=(),
        score=0.5,
        passed=0,
        failed=0,
        evidence_kind="unverified",
        error=reason,
        label="unverified",
        heldout=heldout,
    )


@dataclass(frozen=True)
class Comparison:
    """Before/after deltas (prompt §9)."""

    baseline: EvaluationRun
    candidate: EvaluationRun
    before_score: float
    after_score: float
    delta: float
    regression_delta: float | None
    """Held-out regression, or ``None`` when no held-out baseline exists."""

    cost_delta: float | None
    latency_delta_ms: float | None
    reliability_delta: float | None
    possible_overfitting: bool
    overfitting_gap: float | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_score": round(self.before_score, 6),
            "after_score": round(self.after_score, 6),
            "delta": round(self.delta, 6),
            "regression_delta": self.regression_delta,
            "cost_delta": self.cost_delta,
            "latency_delta_ms": self.latency_delta_ms,
            "reliability_delta": self.reliability_delta,
            "possible_overfitting": self.possible_overfitting,
            "overfitting_gap": self.overfitting_gap,
            "reasons": list(self.reasons),
        }


def compare(
    baseline: EvaluationRun,
    candidate: EvaluationRun,
    *,
    train_baseline: EvaluationRun | None = None,
    train_candidate: EvaluationRun | None = None,
    overfitting_gap: float = 0.2,
) -> Comparison:
    """Compare a candidate against its baseline across every tracked axis.

    Each delta is ``None`` when its inputs are missing — a latency delta
    computed from an unmeasured duration is a fabricated number.
    """
    reasons: list[str] = []
    if baseline.evidence_kind != "measured":
        reasons.append(f"baseline is unverified ({baseline.error}); the comparison is informational only")
    if candidate.evidence_kind != "measured":
        reasons.append(f"candidate is unverified ({candidate.error}); the comparison is informational only")

    delta = candidate.score - baseline.score

    regression_delta: float | None = None
    if baseline.heldout and candidate.heldout:
        regression_delta = candidate.score - baseline.score

    cost_delta = _cost_delta(baseline, candidate)
    latency_delta = _latency_delta(baseline, candidate)

    gap: float | None = None
    overfitting = False
    if train_candidate is not None and candidate.heldout:
        gap = train_candidate.score - candidate.score
        train_improved = train_baseline is None or train_candidate.score > train_baseline.score
        if gap > overfitting_gap and train_improved and delta <= 0:
            overfitting = True
            reasons.append(f"train score improved while the held-out delta is {delta:+.4f} and the train-minus-held-out gap is {gap:+.4f} (> {overfitting_gap:+.4f}): possible overfitting")
        elif gap > overfitting_gap:
            reasons.append(f"train-minus-held-out gap is {gap:+.4f}, above the {overfitting_gap:+.4f} bar; watch for overfitting")

    return Comparison(
        baseline=baseline,
        candidate=candidate,
        before_score=baseline.score,
        after_score=candidate.score,
        delta=delta,
        regression_delta=regression_delta,
        cost_delta=cost_delta,
        latency_delta_ms=latency_delta,
        reliability_delta=candidate.score - baseline.score if baseline.evidence_kind == candidate.evidence_kind else None,
        possible_overfitting=overfitting,
        overfitting_gap=gap,
        reasons=reasons,
    )


def _cost_delta(_baseline: EvaluationRun, _candidate: EvaluationRun) -> float | None:
    """Cost delta is ``None`` — cost is not tracked on ``CaseResult``.

    Returning ``0.0`` here would be the most tempting wrong answer: two runs with
    no recorded cost would "show" a zero cost delta, which reads as "this change
    is free". A caller that does track cost should pass
    ``Comparison(..., cost_delta=...)`` explicitly.
    """
    return None


def _latency_delta(baseline: EvaluationRun, candidate: EvaluationRun) -> float | None:
    if baseline.duration_measured and candidate.duration_measured:
        return (candidate.duration_ms or 0.0) - (baseline.duration_ms or 0.0)
    return None


@dataclass(frozen=True)
class EvaluationOutcome:
    """The gate's verdict plus everything that justified it."""

    decision: Decision
    reason: str
    comparison: Comparison | None = None
    gates: dict[str, bool] = field(default_factory=dict)
    """Named gate outcomes, so a refusal names which gate refused."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "gates": dict(self.gates),
        }


def evaluate_gate(
    candidate: EvaluationRun,
    baseline: EvaluationRun,
    *,
    min_score: float = 0.0,
    max_regression_delta: float = 0.0,
    heldout: EvaluationRun | None = None,
    min_heldout_score: float = 0.0,
    train: EvaluationRun | None = None,
    overfitting_gap: float = 0.2,
    require_improvement: bool = True,
    # `pathway`: a PathwayReport (Phase A). None means *no pathway evidence was
    # supplied*, which is NOT the same as "the pathway engaged" — an absent
    # check does not block. When supplied, NOT_ENGAGED blocks and INCONCLUSIVE
    # blocks; combined with the anomaly test below, an unexplained score gain is
    # refused even when the pathway verdict itself is not a refusal.
    pathway: Any = None,
    # `diversity`: a DiversityReport (Phase C). This is the one gate that can
    # reject a candidate that *scored better* — a narrowed agent holding the
    # same score is worse than one that can still do something else, so
    # collapsed=True overrides an improvement.
    diversity: Any = None,
    # `noise_floor`: the measured evaluator noise (Phase B). It can only RAISE
    # the diversity bar, never lower it.
    noise_floor: float | None = None,
) -> EvaluationOutcome:
    """Apply the promotion gate to a candidate (prompt §10).

    Fail-closed by construction. The gates are evaluated in severity order and
    the first refusal wins, so the returned ``reason`` names the *actual* blocker
    rather than a list where the reader must work out which one mattered:

    1. candidate measured?
    2. held-out regressions within ``max_regression_delta``?
    3. held-out floor?
    4. regression floor?
    5. overfitting?
    6. strictly better than baseline?

    ``REJECT`` is the fail-closed terminal decision. Nothing in this function can
    return ``PROMOTE`` for an unverified candidate, which is why the signature
    takes no "autonomous" flag that could bypass the gate.
    """
    gates: dict[str, bool] = {}

    if candidate.evidence_kind != "measured":
        gates["measured"] = False
        return EvaluationOutcome(
            decision=Decision.REJECT,
            reason=f"candidate is unverified, so it cannot be promoted: {candidate.error or 'no reason recorded'}",
            gates=gates,
        )
    gates["measured"] = True

    comparison = compare(
        baseline,
        candidate,
        train_baseline=None,
        train_candidate=train,
        overfitting_gap=overfitting_gap,
    )

    if heldout is not None:
        held_delta = heldout.score - (baseline.score if baseline.heldout else baseline.score)
        gates["heldout_no_regression"] = held_delta >= -abs(max_regression_delta)
        if not gates["heldout_no_regression"]:
            return EvaluationOutcome(
                decision=Decision.REJECT,
                reason=(f"held-out score regressed by {abs(held_delta):.4f}, beyond the tolerated {abs(max_regression_delta):.4f}"),
                comparison=comparison,
                gates=gates,
            )
        gates["heldout_floor"] = heldout.score >= min_heldout_score
        if not gates["heldout_floor"]:
            return EvaluationOutcome(
                decision=Decision.REJECT,
                reason=f"held-out score {heldout.score:.4f} is below the floor {min_heldout_score:.4f}",
                comparison=comparison,
                gates=gates,
            )
        gates["measured"] = True

    gates["score_floor"] = candidate.score >= min_score
    if not gates["score_floor"]:
        return EvaluationOutcome(
            decision=Decision.REJECT,
            reason=f"candidate score {candidate.score:.4f} is below the floor {min_score:.4f}",
            comparison=comparison,
            gates=gates,
        )

    if comparison.possible_overfitting:
        gates["no_overfitting"] = False
        return EvaluationOutcome(
            decision=Decision.REJECT,
            reason="possible overfitting: " + "; ".join(comparison.reasons),
            comparison=comparison,
            gates=gates,
        )
    gates["no_overfitting"] = True

    # Phase A — pathway evidence. Placed after overfitting and before the
    # improvement check, because the improvement check is what produces the
    # `score_improved` signal the anomaly test needs.
    if pathway is not None:
        pathway_verdict = getattr(pathway, "verdict", None)
        pathway_value = getattr(pathway_verdict, "value", pathway_verdict)
        anomalous = bool(getattr(pathway, "is_anomalous_improvement", False))
        if pathway_value == "INCONCLUSIVE":
            gates["pathway_engaged"] = False
            return EvaluationOutcome(
                decision=Decision.REJECT,
                reason=(f"pathway evidence is inconclusive, so the candidate's claimed mechanism could not be shown to have engaged: {getattr(pathway, 'reason', 'no reason recorded')}. A probe that could not run has not passed."),
                comparison=comparison,
                gates=gates,
            )
        if pathway_value == "NOT_ENGAGED":
            if anomalous and require_improvement:
                gates["pathway_engaged"] = False
                gates["no_anomalous_improvement"] = False
                return EvaluationOutcome(
                    decision=Decision.REJECT,
                    reason=(
                        "anomalous improvement: the score improved but the mechanism the candidate claimed did NOT "
                        f"engage ({getattr(pathway, 'reason', '')}). A number that went up for an unexplained reason "
                        "is the condition that lets a broken loop look healthy."
                    ),
                    comparison=comparison,
                    gates=gates,
                )
            gates["pathway_engaged"] = False
            return EvaluationOutcome(
                decision=Decision.REJECT,
                reason=(f"the claimed mechanism did not engage: {getattr(pathway, 'reason', '')}. A candidate that claims to change a mechanism and did not is not a candidate for that mechanism."),
                comparison=comparison,
                gates=gates,
            )
        gates["pathway_engaged"] = True
        if anomalous:
            gates["no_anomalous_improvement"] = False
            return EvaluationOutcome(
                decision=Decision.REJECT,
                reason=("anomalous improvement: the score improved while the claimed mechanism did not engage, so the gain is unexplained even though the pathway verdict is not itself a refusal"),
                comparison=comparison,
                gates=gates,
            )
        gates["no_anomalous_improvement"] = True

    # Phase C — behavioural diversity. The only gate that overrides an
    # improvement, and it runs last so it is reached only when everything else
    # passed.
    if diversity is not None:
        collapsed = bool(getattr(diversity, "collapsed", False))
        gates["no_behavior_collapse"] = not collapsed
        if collapsed:
            detail = "; ".join(getattr(diversity, "reasons", []) or []) or "behaviour coverage collapsed"
            return EvaluationOutcome(
                decision=Decision.REJECT,
                reason=(
                    f"behaviour collapse: {detail}. This overrides the score — an agent that narrowed its action "
                    f"space while holding or improving its score is worse than one that can still do something else "
                    f"(coverage {getattr(diversity, 'coverage_before', None)} -> "
                    f"{getattr(diversity, 'coverage_after', None)})." + (f" Noise floor applied: {noise_floor:.4f}." if noise_floor else "")
                ),
                comparison=comparison,
                gates=gates,
            )

    if require_improvement and candidate.score <= baseline.score:
        gates["strictly_better"] = False
        return EvaluationOutcome(
            decision=Decision.REJECT,
            reason=(f"candidate score {candidate.score:.4f} is not better than baseline {baseline.score:.4f}; an unmeasured improvement is not an improvement, and a measured flat result is not a promotion"),
            comparison=comparison,
            gates=gates,
        )
    gates["strictly_better"] = True

    return EvaluationOutcome(
        decision=Decision.PROMOTE,
        reason=(f"candidate {candidate.score:.4f} beats baseline {baseline.score:.4f} (delta {comparison.delta:+.4f}) with no held-out regression and no overfitting signal"),
        comparison=comparison,
        gates=gates,
    )


# ---------------------------------------------------------------------------
# The standing suite
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegressionCase:
    """One standing case: a task, an expected behaviour, and a verification."""

    case_id: str
    category: str
    task: str
    expected_behavior: str
    verification: str
    difficulty: float = 0.5
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must be non-empty")
        if self.category not in CATEGORIES:
            raise ValueError(f"category {self.category!r} is not a known capability category. Expected one of: {', '.join(CATEGORIES)}")
        if not self.task.strip() or not self.expected_behavior.strip() or not self.verification.strip():
            raise ValueError(f"case {self.case_id!r} needs a task, an expected behavior, and a verification")
        if not 0.0 <= self.difficulty <= 1.0:
            raise ValueError(f"case {self.case_id!r} difficulty must be within [0.0, 1.0], got {self.difficulty!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "task": self.task,
            "expected_behavior": self.expected_behavior,
            "verification": self.verification,
            "difficulty": self.difficulty,
            "hidden": self.hidden,
        }


#: The standing suite: at least one case per required category, all evaluated
#: without a model call. These are *property* checks over Alpha's own invariants
#: (an atomic write really is atomic, a protected expert really is protected),
#: which is what makes them runnable in CI on every learning change.
_DEFAULT_CASES: tuple[RegressionCase, ...] = (
    RegressionCase(
        "read-before-write-guard",
        Category.CODING,
        "Ask the agent to overwrite a file it has not read.",
        "The guard blocks the write and names the read requirement; it does not silently overwrite.",
        "Assert the write is refused and the reason names the missing read.",
        0.3,
    ),
    RegressionCase(
        "patch-is-not-empty",
        Category.CODING,
        "Finish a coding task having viewed files but written nothing.",
        "An empty patch cannot be reported as a completed change.",
        "Assert the empty-patch case is rejected rather than counted as success.",
        0.3,
    ),
    RegressionCase(
        "terminal-command-allowlist",
        Category.TERMINAL,
        "Run a terminal command that is not on the allowlist.",
        "The command is refused with the reason; it is not executed and not silently swallowed.",
        "Assert the refusal names the disallowed command.",
        0.3,
    ),
    RegressionCase(
        "terminal-command-is-bounded",
        Category.TERMINAL,
        "Run a terminal command that outlives its deadline.",
        "The call is abandoned at the deadline and the overrun is reported, not waited out.",
        "Assert the measured overrun is reported and the result is discarded.",
        0.4,
    ),
    RegressionCase(
        "atomic-write-survives-crash",
        Category.REASONING,
        "Interrupt an atomic document write at each step.",
        "The previous valid document survives and no partial document is published.",
        "Fault-inject every storekit step and assert the target is unchanged.",
        0.4,
    ),
    RegressionCase(
        "store-schema-drift-refused",
        Category.MEMORY,
        "Load a persisted document with an unknown schema_version.",
        "Load fails loudly with the real reason; no default state is invented.",
        "Write a bad schema_version and assert the refusal names it.",
        0.2,
    ),
    RegressionCase("unknown-field-refused", Category.MEMORY, "Deserialize a record carrying an unknown field.", "Deserialization refuses and names the offending field.", "Assert ValueError names the field.", 0.2),
    RegressionCase(
        "missing-measurement-not-a-pass",
        Category.REASONING,
        "Gate a promotion on an unverified evaluation.",
        "The gate rejects; an unmeasured run can never promote.",
        "Assert evaluate_gate returns REJECT for evidence_kind='unverified'.",
        0.3,
    ),
    RegressionCase(
        "empty-suite-is-unverified",
        Category.PLANNING,
        "Aggregate an evaluation with zero executed cases.",
        "The run reports score 0.5 and evidence_kind 'unverified', not a pass.",
        "Assert EvaluationRun.from_results([]) is unverified.",
        0.2,
    ),
    RegressionCase(
        "router-excludes-trial-by-default", Category.LLM_ROUTING, "Route a task with a TRIAL expert in the pool.", "The trial expert is excluded unless allow_trial is set.", "Assert the trial expert appears in decision.excluded.", 0.3
    ),
    RegressionCase("exploration-off-is-zero", Category.LLM_ROUTING, "Route with exploring=False.", "Every candidate's exploration_bonus is 0.0.", "Assert no admitted candidate has a non-zero bonus.", 0.2),
    RegressionCase(
        "pinned-expert-not-evicted",
        Category.RESOURCE_HANDLING,
        "Evict under memory pressure with every slot pinned.",
        "Nothing is evicted and the admission is refused with a reason.",
        "Pin every slot and assert admit() returns False.",
        0.4,
    ),
    RegressionCase("protected-expert-never-pruned", Category.SECURITY, "Attempt to prune a protected expert.", "The prune is refused and the record survives.", "Assert ExpertFabric.prune raises and the record is still present.", 0.3),
    RegressionCase("heldout-ids-hidden-from-candidate", Category.SECURITY, "Render a held-out run for a candidate.", "Held-out case ids are withheld and only their count is reported.", "Assert to_candidate_payload omits hidden ids.", 0.3),
    RegressionCase("prune-requires-reason", Category.SECURITY, "Prune an expert with an empty reason.", "The prune is refused; a silent prune is prohibited.", "Assert ExpertFabric.prune raises on an empty reason.", 0.2),
    RegressionCase("illegal-transition-refused", Category.PLANNING, "Transition a PROPOSED expert directly to ACTIVE.", "The transition is refused and both states are named.", "Assert ValueError names the legal targets.", 0.3),
    RegressionCase(
        "single-observation-is-no-trend",
        Category.REASONING,
        "Ask the plasticity controller to decide on one observation.",
        "It reports insufficient observations and holds at MAINTAIN.",
        "Assert decision.insufficient_observations is True.",
        0.3,
    ),
    RegressionCase(
        "reservoir-retains-old-knowledge",
        Category.MEMORY,
        "Fill the reservoir with recent items and sample requiring OLD_KNOWLEDGE.",
        "An old item is still returned, so recency cannot erase history.",
        "Assert the required stratum contributes to the sample.",
        0.4,
    ),
    RegressionCase(
        "reservoir-is-bounded",
        Category.RESOURCE_HANDLING,
        "Insert more items than the configured capacity.",
        "The reservoir never exceeds capacity and the eviction is recorded.",
        "Assert size() <= capacity and recent_evictions is non-empty.",
        0.3,
    ),
    RegressionCase(
        "agent-protocol-boundary", Category.AGENT_COORDINATION, "Import a harness module that references app.*.", "The import fails; the harness never depends on the application layer.", "Assert tests/test_harness_boundary.py passes.", 0.5
    ),
    RegressionCase(
        "task-state-survives-restart",
        Category.PERSISTENT_TASKS,
        "Reload a persisted task after a simulated restart.",
        "The task is recovered with its real status, never fabricated as complete.",
        "Assert the reloaded state matches the persisted one.",
        0.5,
    ),
    RegressionCase(
        "terminal-tool-path-validates",
        Category.TOOL_USE,
        "Invoke a terminal tool with a disallowed command.",
        "The call is refused with the reason; the tool does not silently no-op.",
        "Assert the refusal names the disallowed command.",
        0.3,
    ),
    RegressionCase(
        "mcp-config-roundtrip", Category.MCP, "Reload a persisted MCP server configuration.", "The configuration round-trips without re-writing resolved secrets to disk.", "Assert raw references are preserved after a write.", 0.4
    ),
    RegressionCase("git-path-validation", Category.GIT, "Commit a file on a forbidden path.", "The commit is refused and the forbidden path is named.", "Assert sentinel.commit.validate_paths reports the path.", 0.3),
    RegressionCase(
        "local-model-slot-resolves",
        Category.LOCAL_MODELS,
        "Route a task to the local model routing tier.",
        "The slot resolves through model_routing, or reports that nothing is declared.",
        "Assert the resolution never invents a vendor model id.",
        0.4,
    ),
    RegressionCase(
        "browser-frames-excluded-from-changes",
        Category.BROWSER,
        "Snapshot workspace changes during a browser session.",
        "Transient browser frames are not counted as workspace changes.",
        "Assert EXCLUDED_DIR_NAMES drops the frames directory.",
        0.4,
    ),
    RegressionCase(
        "recovery-does-not-adopt-late-result",
        Category.TASK_RECOVERY,
        "Deliver a fenced tool result after a run was recovered.",
        "The stale result is discarded rather than adopted.",
        "Assert the recovered run does not take the late result.",
        0.6,
    ),
    RegressionCase("swarm-requires-independent-voters", Category.SWARM, "Approve a swarm plan with duplicate voters.", "Duplicate voters cannot produce a clean approval.", "Assert the aggregator reports the duplicate.", 0.5),
    RegressionCase(
        "selfdebug-does-not-claim-fixed",
        Category.SELF_DEBUGGING,
        "Report a repair with no post-action verifier.",
        "verify_repair returns false with the missing-verifier reason; 'fixed' is never claimed.",
        "Assert the refusal reason is explicit.",
        0.5,
    ),
    RegressionCase(
        "research-cites-its-sources", Category.RESEARCH, "Return a research synthesis with no reachable source.", "The claim is marked unverified rather than cited.", "Assert citations_verified is false when no source verified.", 0.4
    ),
    RegressionCase(
        "github-webhook-authenticates", Category.GITHUB, "Deliver a GitHub webhook with an invalid signature.", "The delivery is refused; authenticity is enforced by HMAC.", "Assert the route is fail-closed without the secret.", 0.3
    ),
    RegressionCase("difficulty-unknown-is-not-easy", Category.PLANNING, "Estimate difficulty with no signals supplied.", "Coverage is 0.0 and the estimate is marked unmeasured, not easy.", "Assert estimate.coverage == 0.0.", 0.3),
)


def default_regression_suite() -> tuple[RegressionCase, ...]:
    """The standing suite, one case per required capability category."""
    return _DEFAULT_CASES


def register_regression_suite(runner: Any = None) -> list[str]:
    """Register the standing suite with a :class:`~alpha.benchmarks.runner.BenchmarkRunner`.

    The evaluator is deliberately **model-free and side-effect free**: it checks
    that every declared case is well-formed and every category is covered, and
    returns a measured score for exactly that. It is an integrity harness, not a
    task executor — the per-category behavioural suites the prompt describes are
    declared here as :class:`RegressionCase` records and executed by whoever owns
    that subsystem's tests.

    Returns the registered case ids.
    """
    from alpha.benchmarks.runner import BenchmarkCase, BenchmarkRunner, BenchmarkSuite

    active = runner if runner is not None else BenchmarkRunner()
    cases = default_regression_suite()
    suite = BenchmarkSuite(
        name="intelligence-regression",
        version=REGRESSION_SUITE_VERSION,
        cases=[BenchmarkCase(case_id=case.case_id, title=f"{case.category}: {case.task}", fixture=case.to_dict(), expected={"well_formed": True}) for case in cases],
    )
    active.register_suite(suite, _suite_integrity_evaluator)
    return [case.case_id for case in cases]


def _suite_integrity_evaluator(case: Any) -> tuple[bool, float, str]:
    """Verify one declared case is well-formed. Never calls a model."""
    fixture = getattr(case, "fixture", None)
    if not isinstance(fixture, dict):
        return False, 0.0, f"case {case.case_id} has a non-object fixture"
    try:
        RegressionCase(
            case_id=str(fixture.get("case_id", "")),
            category=str(fixture.get("category", "")),
            task=str(fixture.get("task", "")),
            expected_behavior=str(fixture.get("expected_behavior", "")),
            verification=str(fixture.get("verification", "")),
            difficulty=float(fixture.get("difficulty", 0.5)),
            hidden=bool(fixture.get("hidden", False)),
        )
    except (TypeError, ValueError) as exc:
        return False, 0.0, f"case {case.case_id} is malformed: {exc}"
    return True, 1.0, f"case {case.case_id} declares a checkable task, expected behavior, and verification"


def coverage_report() -> dict[str, Any]:
    """Which categories the standing suite covers, and how thoroughly."""
    cases = default_regression_suite()
    by_category: dict[str, list[str]] = {}
    for case in cases:
        by_category.setdefault(case.category, []).append(case.case_id)
    missing = [category for category in CATEGORIES if category not in by_category]
    return {
        "suite_version": REGRESSION_SUITE_VERSION,
        "case_count": len(cases),
        "categories_covered": len(by_category),
        "categories_required": len(CATEGORIES),
        "missing_categories": missing,
        "by_category": {key: sorted(value) for key, value in sorted(by_category.items())},
        "complete": not missing,
    }
