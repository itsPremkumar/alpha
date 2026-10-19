"""Task complexity estimation and prior-evidence relevance (guide §16 Phase 7).

Phase 7 asks the engine to decide *intelligently* when more experiments are
worth their cost.  Two inputs inform that decision:

* **Task complexity** — how much surface area the task touches (deliverables,
  checks, domain tags, uncertainty count).  A trivial task does not justify a
  multi-candidate run; a complex one does.
* **Prior evidence relevance** — how many lessons in the experience library
  apply to this task's domain/tool/runtime.  High relevance means the library
  already knows something; low relevance means the run is exploratory.

Neither input fabricates a number: complexity is a weighted sum of *declared*
task-contract fields (zero means "no declared surface", not "simple"), and
relevance is the count of retrieved entries that survived the library's own
staleness filter.  Both are advisory — they inform a stop decision, never a
gate that blocks a run the operator asked for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.harness_improvement.experiences import ExperienceMetadata, ExperienceQuery, select_experiences


@dataclass(frozen=True, slots=True)
class TaskComplexity:
    """A measured estimate of how much surface area a task touches.

    Every component is a count of something the task contract actually declared;
    ``None`` means "not declared" and is treated as zero in the weighted sum
    but reported separately so a caller can distinguish "declared zero" from
    "did not declare".
    """

    deliverables: int
    checks: int
    domain_tags: int
    uncertainties: int
    allowed_tools: int
    has_task_type: bool

    @property
    def score(self) -> float:
        """A bounded 0..1 complexity score.

        Weights reflect how much each declared field expands the search space:
        deliverables and checks are the dominant signals (each adds a
        verification surface), domain tags broaden the retrieval context, and
        uncertainties raise the chance that a single hypothesis is wrong.
        """
        raw = 0.30 * min(self.deliverables, 5) + 0.25 * min(self.checks, 5) + 0.15 * min(self.domain_tags, 5) + 0.15 * min(self.uncertainties, 5) + 0.10 * min(self.allowed_tools, 10) + (0.05 if self.has_task_type else 0.0)
        # Normalise so a "maximal" task scores ~1.0.
        max_raw = 0.30 * 5 + 0.25 * 5 + 0.15 * 5 + 0.15 * 5 + 0.10 * 10 + 0.05
        return round(min(1.0, raw / max_raw), 4)

    @property
    def tier(self) -> str:
        """A human-readable tier for UI and telemetry."""
        s = self.score
        if s < 0.2:
            return "trivial"
        if s < 0.4:
            return "simple"
        if s < 0.6:
            return "moderate"
        if s < 0.8:
            return "complex"
        return "heavy"

    def to_dict(self) -> dict[str, Any]:
        return {
            "deliverables": self.deliverables,
            "checks": self.checks,
            "domain_tags": self.domain_tags,
            "uncertainties": self.uncertainties,
            "allowed_tools": self.allowed_tools,
            "has_task_type": self.has_task_type,
            "score": self.score,
            "tier": self.tier,
        }


def estimate_complexity(task: Any) -> TaskComplexity:
    """Estimate complexity from a TaskContract's declared fields.

    ``task`` is typed as ``Any`` so this function can be called from the
    engine (which holds a ``TaskContract``) and from tests without a circular
    import.  Missing attributes are treated as "not declared" (zero).
    """
    deliverables = getattr(task, "deliverables", None) or ()
    checks = getattr(task, "checks", None) or ()
    domain_tags = getattr(task, "domain_tags", None) or ()
    uncertainties = getattr(task, "uncertainties", None) or ()
    allowed_tools = getattr(task, "allowed_tools", None) or ()
    task_type = getattr(task, "task_type", None)
    return TaskComplexity(
        deliverables=len(deliverables),
        checks=len(checks),
        domain_tags=len(domain_tags),
        uncertainties=len(uncertainties),
        allowed_tools=len(allowed_tools),
        has_task_type=bool(task_type and str(task_type).strip()),
    )


@dataclass(frozen=True, slots=True)
class EvidenceRelevance:
    """How much the experience library knows about this task.

    ``retrieved`` is the count of entries that survived the library's own
    staleness filter and matched the query.  ``total`` is the library size.
    Neither is a quality judgement — a high-relevance library may still be
    wrong; a low-relevance one just means the run is exploratory.
    """

    retrieved: int
    total: int
    entries: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(self.retrieved / self.total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieved": self.retrieved,
            "total": self.total,
            "ratio": self.ratio,
            "entry_ids": [e.get("experience_id") for e in self.entries],
        }


def score_evidence_relevance(
    entries: Sequence[ExperienceMetadata],
    *,
    task_type: str | None = None,
    domain_tags: tuple[str, ...] = (),
    text: str = "",
    alpha_runtime_version: str = "",
    tool_schema_version: str = "",
    limit: int = 5,
    now: str | None = None,
) -> EvidenceRelevance:
    """Score how relevant the experience library is to this task.

    Uses the library's own ``select_experiences`` (which applies staleness
    filtering), so a stale entry never counts as prior evidence.
    """
    query = ExperienceQuery(
        task_type=task_type,
        domain_tags=domain_tags,
        text=text,
        alpha_runtime_version=alpha_runtime_version,
        tool_schema_version=tool_schema_version,
        limit=limit,
    )
    rows = select_experiences(entries, query, now=now)
    return EvidenceRelevance(
        retrieved=len(rows),
        total=len(entries),
        entries=tuple(rows),
    )


@dataclass(frozen=True, slots=True)
class StopDecision:
    """Whether a run should stop early (adaptive recursion).

    ``should_stop`` is advisory — the engine records it and may skip the
    successor stage, but the operator can still resume.  ``reason`` names
    why (or why not).  ``marginal_improvement`` is the signal from the
    composition stage.
    """

    should_stop: bool
    reason: str
    complexity: TaskComplexity
    evidence_relevance: EvidenceRelevance
    marginal_improvement: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_stop": self.should_stop,
            "reason": self.reason,
            "complexity": self.complexity.to_dict(),
            "evidence_relevance": self.evidence_relevance.to_dict(),
            "marginal_improvement": self.marginal_improvement,
        }


def decide_stop(
    *,
    task: Any,
    marginal_improvement: bool | None,
    entries: Sequence[ExperienceMetadata] = (),
    alpha_runtime_version: str = "",
    tool_schema_version: str = "",
    min_complexity_for_recursion: float = 0.3,
    min_evidence_relevance: float = 0.0,
) -> StopDecision:
    """Decide whether a run should stop (no more recursion).

    Stop when:
    1. No marginal improvement was found (composer retained the baseline).
    2. Task complexity is below the threshold for recursion.
    3. A probe added no information (no marginal improvement AND no
       retrieved evidence).

    Never stop when the operator explicitly asked for a run — this is
    advisory, and the run still completes normally.  The decision is recorded
    in the run record for telemetry and UI.
    """
    complexity = estimate_complexity(task)
    relevance = score_evidence_relevance(
        entries,
        task_type=getattr(task, "task_type", None),
        domain_tags=tuple(getattr(task, "domain_tags", ()) or ()),
        text=getattr(task, "outcome", "") or "",
        alpha_runtime_version=alpha_runtime_version,
        tool_schema_version=tool_schema_version,
    )

    if marginal_improvement is False:
        return StopDecision(
            should_stop=True,
            reason="no marginal improvement: composer retained the baseline",
            complexity=complexity,
            evidence_relevance=relevance,
            marginal_improvement=marginal_improvement,
        )

    if complexity.score < min_complexity_for_recursion:
        return StopDecision(
            should_stop=True,
            reason=f"task complexity {complexity.score} below recursion threshold {min_complexity_for_recursion}",
            complexity=complexity,
            evidence_relevance=relevance,
            marginal_improvement=marginal_improvement,
        )

    if marginal_improvement is None and relevance.retrieved == 0:
        return StopDecision(
            should_stop=True,
            reason="no marginal improvement and no retrieved evidence: probe added no information",
            complexity=complexity,
            evidence_relevance=relevance,
            marginal_improvement=marginal_improvement,
        )

    return StopDecision(
        should_stop=False,
        reason="improvement found or sufficient complexity/evidence to continue",
        complexity=complexity,
        evidence_relevance=relevance,
        marginal_improvement=marginal_improvement,
    )


__all__ = [
    "EvidenceRelevance",
    "StopDecision",
    "TaskComplexity",
    "decide_stop",
    "estimate_complexity",
    "score_evidence_relevance",
]
