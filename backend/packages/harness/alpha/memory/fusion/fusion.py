"""Section-11 weighted fusion and optional deterministic reciprocal-rank fusion."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from .config import DEFAULT_FUSION_WEIGHTS, FusionConfig
from .models import Candidate, DroppedItem, FusedCandidate, FusionResult, StageResult
from .query import TaskContext

DEFAULT_RRF_K = 60
STAGE_ORDER = ("exact", "semantic", "graph", "temporal", "procedural")


def _longest_text(candidates: Sequence[Candidate], attribute: str) -> str:
    values = [str(getattr(candidate, attribute) or "").strip() for candidate in candidates]
    return max(values, key=lambda value: (len(value), value), default="")


def _min_present(candidates: Sequence[Candidate], attribute: str) -> float | None:
    values = [float(getattr(candidate, attribute)) for candidate in candidates if getattr(candidate, attribute) is not None]
    return min(values, default=None)


def _max_present(candidates: Sequence[Candidate], attribute: str) -> float | None:
    values = [float(getattr(candidate, attribute)) for candidate in candidates if getattr(candidate, attribute) is not None]
    return max(values, default=None)


def _union(candidates: Sequence[Candidate], attribute: str) -> tuple[str, ...]:
    values = {str(item) for candidate in candidates for item in getattr(candidate, attribute)}
    return tuple(sorted(values))


def merge_candidates(candidates: Sequence[Candidate]) -> Candidate:
    """Deterministically merge observations that share one id and scope."""
    if not candidates:
        raise ValueError("cannot merge an empty candidate sequence")
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -len(candidate.searchable_text),
            -candidate.authority,
            -candidate.recency,
            candidate.id,
            candidate.model_dump_json(),
        ),
    )
    canonical = ordered[0]
    score_components: dict[str, float] = {}
    for candidate in ordered:
        for key, value in candidate.score_components.items():
            score_components[key] = max(score_components.get(key, value), value)

    metadata = dict(canonical.metadata)
    for candidate in reversed(ordered[1:]):
        for key in sorted(candidate.metadata):
            metadata.setdefault(key, candidate.metadata[key])

    valid_to_values = [candidate.valid_to for candidate in ordered]
    valid_to = None if any(value is None for value in valid_to_values) else max(valid_to_values)
    return Candidate(
        id=canonical.id,
        memory_type=canonical.memory_type,
        subtype=canonical.subtype,
        content=_longest_text(ordered, "content"),
        summary=_longest_text(ordered, "summary") or None,
        score_components=score_components,
        scope_id=canonical.scope_id,
        created_at=_min_present(ordered, "created_at"),
        updated_at=_max_present(ordered, "updated_at"),
        event_time=_max_present(ordered, "event_time"),
        valid_from=_max_present(ordered, "valid_from"),
        valid_to=valid_to,
        last_accessed_at=_max_present(ordered, "last_accessed_at"),
        access_count=max(candidate.access_count for candidate in ordered),
        authority=max(candidate.authority for candidate in ordered),
        source_refs=_union(ordered, "source_refs"),
        provenance_refs=_union(ordered, "provenance_refs"),
        tags=_union(ordered, "tags"),
        evidence_group=canonical.evidence_group,
        contradiction_group=canonical.contradiction_group,
        supersedes=_union(ordered, "supersedes"),
        superseded_by=_union(ordered, "superseded_by"),
        metadata=metadata,
    )


def _component_values(candidate: Candidate, ctx: TaskContext | None) -> tuple[dict[str, float], tuple[str, ...]]:
    values: dict[str, float] = {}
    missing: list[str] = []
    for component in DEFAULT_FUSION_WEIGHTS:
        if component == "scope_match" and ctx is not None:
            if candidate.scope_id is None:
                values[component] = 0.0
                missing.append(component)
            else:
                values[component] = 1.0 if candidate.scope_id == ctx.scope_id else 0.0
            continue
        raw = candidate.score_components.get(component)
        if raw is None:
            missing.append(component)
        else:
            values[component] = float(raw)
    return values, tuple(missing)


def calculate_weighted_score(
    candidate: Candidate,
    weights: Mapping[str, float] | None = None,
    *,
    task_context: TaskContext | None = None,
) -> float:
    """Apply the section-11 positive weights minus both disclosed penalties.

    With default weights this is exactly
    ``.30 semantic + .20 lexical + .15 graph + .10 temporal + .10 scope +
    .05 importance + .05 confidence + .03 task + .02 access - redundancy -
    contradiction``. A missing positive component is zero, never inferred.
    """
    active_weights = dict(DEFAULT_FUSION_WEIGHTS if weights is None else weights)
    values, _missing = _component_values(candidate, task_context)
    weighted = sum(active_weights.get(component, 0.0) * values.get(component, 0.0) for component in DEFAULT_FUSION_WEIGHTS)
    redundancy = max(0.0, float(candidate.score_components.get("redundancy_penalty", 0.0)))
    contradiction = max(0.0, float(candidate.score_components.get("contradiction_penalty", 0.0)))
    return weighted - redundancy - contradiction


def _weighted_details(
    candidate: Candidate,
    config: FusionConfig,
    ctx: TaskContext | None,
) -> tuple[float, dict[str, float], dict[str, float], dict[str, float], tuple[str, ...]]:
    values, missing = _component_values(candidate, ctx)
    contributions = {component: config.weights.get(component, 0.0) * values.get(component, 0.0) for component in DEFAULT_FUSION_WEIGHTS}
    redundancy = max(0.0, float(candidate.score_components.get("redundancy_penalty", 0.0)))
    contradiction = max(0.0, float(candidate.score_components.get("contradiction_penalty", 0.0)))
    penalties = {"redundancy_penalty": -redundancy, "contradiction_penalty": -contradiction}
    stage_contributions = {
        "exact": contributions.get("lexical", 0.0),
        "semantic": contributions.get("semantic", 0.0),
        "graph": contributions.get("graph", 0.0),
        "temporal": contributions.get("temporal", 0.0),
        "procedural": contributions.get("task_relevance", 0.0),
        "metadata": sum(contributions.get(component, 0.0) for component in ("scope_match", "importance", "confidence", "access_history")),
    }
    return sum(contributions.values()) - redundancy - contradiction, contributions, stage_contributions, penalties, missing


def _rrf_details(
    candidate: Candidate,
    ranks_by_stage: Mapping[str, int],
    ctx: TaskContext | None,
) -> tuple[float, dict[str, float], dict[str, float], tuple[str, ...]]:
    contributions = {stage: 1.0 / (DEFAULT_RRF_K + rank) for stage, rank in sorted(ranks_by_stage.items())}
    _values, missing = _component_values(candidate, ctx)
    redundancy = max(0.0, float(candidate.score_components.get("redundancy_penalty", 0.0)))
    contradiction = max(0.0, float(candidate.score_components.get("contradiction_penalty", 0.0)))
    penalties = {"redundancy_penalty": -redundancy, "contradiction_penalty": -contradiction}
    score = sum(contributions.values()) - redundancy - contradiction
    return score, contributions, penalties, missing


def fuse_stage_results(
    stage_results: Sequence[StageResult],
    config: FusionConfig | None = None,
    *,
    task_context: TaskContext | None = None,
) -> FusionResult:
    """Fuse only candidates actually returned by successful stages.

    Observations are grouped by ``(scope_id, id)``. Weighted fusion uses the
    exact section-11 defaults; RRF uses ``1 / (60 + rank)`` per supporting
    stage. Both subtract candidate-provided redundancy/contradiction penalties
    and sort by descending score then ascending id for deterministic ties.
    """
    active = config or FusionConfig()
    grouped: dict[tuple[str | None, str], list[tuple[str, Candidate]]] = defaultdict(list)
    ranks: dict[tuple[str | None, str], dict[str, int]] = defaultdict(dict)
    stages_run: list[str] = []
    unavailable: dict[str, str] = {}
    dropped: list[DroppedItem] = []

    for result in stage_results:
        if result.status == "unavailable":
            unavailable[result.stage] = result.reason or "unavailable_reason_missing"
            continue
        stages_run.append(result.stage)
        retained = result.candidates[: active.max_candidates_per_stage]
        for candidate in result.candidates[active.max_candidates_per_stage :]:
            dropped.append(DroppedItem(candidate_id=candidate.id, reason="max_candidates_per_stage", details={"stage": result.stage}))
        for rank, candidate in enumerate(retained):
            key = (candidate.scope_id, candidate.id)
            grouped[key].append((result.stage, candidate))
            ranks[key].setdefault(result.stage, rank)

    fused: list[FusedCandidate] = []
    for key in sorted(grouped, key=lambda item: (item[0] or "", item[1])):
        observations = grouped[key]
        candidate = merge_candidates([observation for _stage, observation in observations])
        source_stages = tuple(sorted({stage for stage, _candidate in observations}))
        if active.strategy == "weighted":
            score, weighted, stage_contributions, penalties, missing = _weighted_details(candidate, active, task_context)
        else:
            score, stage_contributions, penalties, missing = _rrf_details(candidate, ranks[key], task_context)
            weighted = {}
        fused.append(
            FusedCandidate(
                **candidate.model_dump(),
                fused_score=score,
                weighted_contributions=weighted,
                stage_contributions=stage_contributions,
                penalty_contributions=penalties,
                missing_components=missing,
                source_stages=source_stages,
            )
        )

    fused.sort(key=lambda candidate: (-candidate.fused_score, candidate.id))
    retained_results = fused[: active.max_results]
    for candidate in fused[active.max_results :]:
        dropped.append(DroppedItem(candidate_id=candidate.id, reason="max_results", details={"score": candidate.fused_score}))
    total_latency = sum(result.latency_ms for result in stage_results)
    exceeded = total_latency > active.latency_budget_ms
    disclosure = "latency_budget_exceeded" if exceeded else "within_latency_budget"
    return FusionResult(
        candidates=tuple(retained_results),
        strategy=active.strategy,
        stages_run=tuple(stages_run),
        stages_unavailable=unavailable,
        total_latency_ms=total_latency,
        latency_budget_ms=active.latency_budget_ms,
        latency_budget_exceeded=exceeded,
        latency_disclosure=disclosure,
        dropped_with_reason=tuple(dropped),
    )


class MemoryFusion:
    """Small reusable facade; configuration and providers remain caller-owned."""

    def __init__(self, config: FusionConfig | None = None) -> None:
        self.config = config or FusionConfig()

    def fuse(
        self,
        stage_results: Sequence[StageResult],
        *,
        task_context: TaskContext | None = None,
    ) -> FusionResult:
        return fuse_stage_results(stage_results, self.config, task_context=task_context)


__all__ = [
    "DEFAULT_RRF_K",
    "MemoryFusion",
    "STAGE_ORDER",
    "calculate_weighted_score",
    "fuse_stage_results",
    "merge_candidates",
]
