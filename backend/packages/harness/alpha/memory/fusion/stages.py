"""Provider-neutral exact, semantic, graph, temporal, and procedural stages.

Every provider is injected. A missing or failing provider yields an explicit
``unavailable`` :class:`StageResult`; it is never represented as an empty match
set. The graph stage owns traversal bounds and cycle protection even when the
provider returns a larger graph batch.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from .models import Candidate, GraphBatch, RetrievalMode, StageResult
from .query import TaskContext

MAX_GRAPH_DEPTH = 3


class CandidateProvider(Protocol):
    """Callable adapter contract shared by the four non-graph stages."""

    def __call__(
        self,
        query: str,
        mode: RetrievalMode,
        budget: int,
        ctx: TaskContext,
    ) -> Iterable[Candidate | Mapping[str, Any]]: ...


class GraphProvider(Protocol):
    """Callable adapter contract for a seed/adjacency/candidate graph batch."""

    def __call__(
        self,
        query: str,
        mode: RetrievalMode,
        budget: int,
        ctx: TaskContext,
    ) -> GraphBatch | Mapping[str, Any]: ...


class RetrievalStage(Protocol):
    """One independently reportable stage in the multi-stage read pipeline."""

    name: str

    def retrieve(self, query: str, mode: RetrievalMode, budget: int, ctx: TaskContext) -> StageResult: ...


def _failure_reason(stage: str, exc: Exception) -> str:
    detail = " ".join(str(exc).split())[:240]
    return f"{stage}_provider_failed:{type(exc).__name__}:{detail}"


def _coerce_rows(raw: object) -> tuple[list[Candidate], int]:
    if isinstance(raw, Mapping) and "id" in raw and "memory_type" in raw:
        raw = [raw]
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise TypeError("provider must return an iterable of candidates")
    candidates: list[Candidate] = []
    invalid = 0
    for item in raw:
        try:
            candidates.append(item if isinstance(item, Candidate) else Candidate.model_validate(item))
        except (TypeError, ValueError, ValidationError):
            invalid += 1
    return candidates, invalid


def _bounded_reason(name: str, count: int, invalid: int, capped: int) -> str:
    details = [f"provider_returned={count}"]
    if invalid:
        details.append(f"discarded_invalid={invalid}")
    if capped:
        details.append(f"discarded_over_stage_cap={capped}")
    return f"{name}_stage:" + ";".join(details)


class _SingleProviderStage:
    """Shared honest-degradation implementation for non-graph stages."""

    name = "single"

    def __init__(self, provider: CandidateProvider | None) -> None:
        self._provider = provider

    def retrieve(self, query: str, mode: RetrievalMode, budget: int, ctx: TaskContext) -> StageResult:
        started = time.perf_counter()
        if self._provider is None:
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason=f"{self.name}_provider_unavailable",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        if not (query or "").strip():
            return StageResult(
                stage=self.name,
                status="empty",
                reason="empty_query",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        if budget <= 0:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="non_positive_budget",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        try:
            raw = self._provider(query, mode, budget, ctx)
            candidates, invalid = _coerce_rows(raw)
        except Exception as exc:  # noqa: BLE001 - failure is represented as unavailable
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason=_failure_reason(self.name, exc),
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        capped = max(0, len(candidates) - ctx.max_candidates)
        retained = candidates[: ctx.max_candidates]
        if not retained:
            reason = f"{self.name}_provider_returned_no_valid_candidates" if invalid else f"{self.name}_provider_returned_no_candidates"
            status = "unavailable" if invalid else "empty"
            return StageResult(
                stage=self.name,
                status=status,
                reason=reason,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        return StageResult(
            stage=self.name,
            candidates=tuple(retained),
            status="ok",
            reason=_bounded_reason(self.name, len(candidates), invalid, capped),
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


class ExactStage(_SingleProviderStage):
    """IDs, filenames, error strings, APIs, versions, env vars, and symbols."""

    name = "exact"


class SemanticStage(_SingleProviderStage):
    """Concepts, paraphrases, preferences, and broad project questions."""

    name = "semantic"


class TemporalStage(_SingleProviderStage):
    """Latest, as-of, and changed-in-window event/validity recall."""

    name = "temporal"


class ProceduralStage(_SingleProviderStage):
    """Skills, cases, successful trajectories, and solution workflows."""

    name = "procedural"


class GraphStage:
    """Bounded multi-hop graph expansion with a visited-node cycle guard."""

    name = "graph"

    def __init__(self, provider: GraphProvider | None) -> None:
        self._provider = provider

    def retrieve(self, query: str, mode: RetrievalMode, budget: int, ctx: TaskContext) -> StageResult:
        started = time.perf_counter()
        if self._provider is None:
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason="graph_provider_unavailable",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        if not (query or "").strip():
            return StageResult(
                stage=self.name,
                status="empty",
                reason="empty_query",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        if budget <= 0:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="non_positive_budget",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        try:
            raw = self._provider(query, mode, budget, ctx)
            batch = raw if isinstance(raw, GraphBatch) else GraphBatch.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - graph failure is unavailable
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason=_failure_reason(self.name, exc),
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        seeds = set(batch.seed_ids)
        seeds.update(ctx.graph_seed_ids)
        if not seeds:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="graph_provider_returned_no_seeds",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        max_depth = min(ctx.graph_max_depth, MAX_GRAPH_DEPTH)
        max_nodes = ctx.graph_max_nodes
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        for seed in sorted(seeds):
            if len(visited) >= max_nodes:
                break
            visited.add(seed)
            queue.append((seed, 0))

        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for neighbor in sorted(set(batch.adjacency.get(current, ()))):
                if neighbor in visited:
                    continue
                if len(visited) >= max_nodes:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))

        mapped_candidates = tuple(batch.candidates[node_id] for node_id in sorted(visited) if node_id in batch.candidates)
        if not mapped_candidates:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="graph_traversal_returned_no_mapped_candidates",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        candidates = mapped_candidates[: ctx.max_candidates]
        capped = len(mapped_candidates) - len(candidates)
        reason = (
            f"graph_stage:visited={len(visited)};max_depth={max_depth};"
            f"max_nodes={max_nodes};mapped_candidates={len(mapped_candidates)}"
        )
        if capped:
            reason += f";discarded_over_stage_cap={capped}"
        return StageResult(
            stage=self.name,
            candidates=candidates,
            status="ok",
            reason=reason,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


def run_retrieval_stages(
    stages: Iterable[RetrievalStage],
    *,
    query: str,
    mode: RetrievalMode,
    budget: int,
    ctx: TaskContext,
    latency_budget_ms: float | None = None,
) -> list[StageResult]:
    """Run stages in caller order and disclose every unattempted stage.

    The helper stops launching new synchronous providers after the sum of
    already-reported stage latency reaches ``latency_budget_ms``. It cannot
    preempt a provider that is already running; that overrun remains visible in
    the returned latency and is reported by :func:`alpha.memory.fusion.fusion.fuse_stage_results`.
    """
    if latency_budget_ms is not None and latency_budget_ms < 0.0:
        raise ValueError("latency_budget_ms must be non-negative")
    results: list[StageResult] = []
    elapsed = 0.0
    exhausted = False
    for stage in stages:
        if latency_budget_ms is not None and elapsed >= latency_budget_ms:
            exhausted = True
        if exhausted:
            results.append(
                StageResult(
                    stage=stage.name,
                    status="unavailable",
                    reason=f"latency_budget_exhausted_before_stage:{stage.name}",
                )
            )
            continue
        result = stage.retrieve(query, mode, budget, ctx)
        results.append(result)
        elapsed += result.latency_ms
    return results


__all__ = [
    "CandidateProvider",
    "ExactStage",
    "GraphProvider",
    "GraphStage",
    "MAX_GRAPH_DEPTH",
    "ProceduralStage",
    "RetrievalStage",
    "SemanticStage",
    "TemporalStage",
    "run_retrieval_stages",
]
