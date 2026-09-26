"""Scope-safe, evidence-grouped, per-memory-type context composition."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence

from .config import BUDGET_TYPE_ORDER, FusionConfig, allocate_type_budgets
from .diversity import select_diverse
from .fusion import merge_candidates
from .models import Candidate, ComposedContext, ContextBlock, DroppedItem, EvidenceGroup
from .query import TaskContext

TokenEstimator = Callable[[str], int]


def deterministic_token_estimate(text: str) -> int:
    """Estimate tokens as UTF-8 bytes / 4, rounded up (minimum one).

    This intentionally simple estimator is deterministic, local, and suitable
    for tests. Hosts may inject the same real tokenizer used by their model.
    It measures the exact composed string; no hidden wrapper or truncation is
    included in :attr:`ComposedContext.total_tokens`.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def _compact(value: str) -> str:
    """Collapse layout whitespace without truncating candidate meaning."""
    return " ".join((value or "").split())


def _drop(candidate: Candidate, reason: str, **details: object) -> DroppedItem:
    return DroppedItem(candidate_id=candidate.id, reason=reason, details={"memory_type": candidate.memory_type, **details})


def _scope_filter(candidates: Sequence[Candidate], ctx: TaskContext) -> tuple[list[Candidate], list[DroppedItem]]:
    kept: list[Candidate] = []
    dropped: list[DroppedItem] = []
    for candidate in candidates:
        if not candidate.scope_id:
            dropped.append(_drop(candidate, "scope_missing"))
        elif candidate.scope_id != ctx.scope_id:
            dropped.append(_drop(candidate, "scope_filtered", requested_scope=ctx.scope_id))
        else:
            kept.append(candidate)
    return kept, dropped


def _drop_stale_and_superseded(candidates: Sequence[Candidate], now: float) -> tuple[list[Candidate], list[DroppedItem]]:
    superseded = {target for candidate in candidates for target in candidate.supersedes}
    kept: list[Candidate] = []
    dropped: list[DroppedItem] = []
    for candidate in candidates:
        if candidate.valid_to is not None and candidate.valid_to <= now:
            dropped.append(_drop(candidate, "stale_validity", valid_to=candidate.valid_to, now=now))
        elif candidate.valid_from is not None and candidate.valid_from > now:
            dropped.append(_drop(candidate, "not_yet_valid", valid_from=candidate.valid_from, now=now))
        elif candidate.superseded_by or candidate.id in superseded:
            dropped.append(_drop(candidate, "superseded"))
        else:
            kept.append(candidate)
    return kept, dropped


def _merge_duplicate_groups(candidates: Sequence[Candidate]) -> tuple[list[Candidate], list[DroppedItem]]:
    by_id: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_id.setdefault(candidate.id, []).append(candidate)

    id_merged: list[Candidate] = []
    dropped: list[DroppedItem] = []
    for candidate_id in sorted(by_id):
        group = by_id[candidate_id]
        merged = merge_candidates(group)
        if len(group) > 1:
            merged_data = merged.model_dump()
            merged_data["metadata"] = {**merged.metadata, "merged_observation_count": len(group)}
            merged = Candidate(**merged_data)
            dropped.extend(_drop(candidate, "duplicate_merged", merged_into=merged.id) for candidate in group[1:])
        id_merged.append(merged)

    by_content: dict[tuple[str, str], list[Candidate]] = {}
    for candidate in id_merged:
        normalized = _compact(candidate.searchable_text).casefold()
        by_content.setdefault((candidate.memory_type.casefold(), normalized), []).append(candidate)

    content_merged: list[Candidate] = []
    for key in sorted(by_content):
        group = by_content[key]
        merged = merge_candidates(group)
        loser_ids = sorted(candidate.id for candidate in group if candidate.id != merged.id)
        if loser_ids:
            merged_data = merged.model_dump()
            merged_data["metadata"] = {**merged.metadata, "merged_duplicate_ids": loser_ids}
            merged = Candidate(**merged_data)
            dropped.extend(
                _drop(candidate, "duplicate_merged", merged_into=merged.id)
                for candidate in group
                if candidate.id != merged.id
            )
        content_merged.append(merged)
    return content_merged, dropped


def _evidence_group(candidate: Candidate) -> str:
    if candidate.evidence_group:
        return candidate.evidence_group
    if candidate.contradiction_group:
        return f"contradiction:{candidate.contradiction_group}"
    if candidate.provenance_refs:
        return candidate.provenance_refs[0]
    if candidate.source_refs:
        return candidate.source_refs[0]
    return "unlinked"


def _candidate_line(candidate: Candidate) -> str:
    text = _compact(candidate.summary or candidate.content)
    return f"- {candidate.id}: {text}"


def _type_heading(memory_type: str) -> str:
    return f"### {_compact(memory_type.replace('_', ' ')).title()}"


def compose_context(
    candidates: Sequence[Candidate],
    config: FusionConfig | None = None,
    *,
    task_context: TaskContext,
    token_estimator: TokenEstimator | None = None,
) -> ComposedContext:
    """Run the section-12 pipeline and compose only complete evidence blocks.

    The effective budget is the smaller of ``TaskContext.budget`` and
    ``FusionConfig.total_budget_tokens``. It is allocated exactly by profile
    before rendering. Candidate text is compacted by whitespace normalization
    but never character-truncated; an evidence group that cannot fit is dropped
    as a whole with a reason.
    """
    active = config or FusionConfig()
    estimate = token_estimator or deterministic_token_estimate
    effective_budget = min(task_context.budget, active.total_budget_tokens)
    type_budgets = allocate_type_budgets(effective_budget, active.profile)
    dropped: list[DroppedItem] = []

    scoped, scope_drops = _scope_filter(candidates, task_context)
    dropped.extend(scope_drops)
    now = float(task_context.as_of if task_context.as_of is not None else time.time())
    current, stale_drops = _drop_stale_and_superseded(scoped, now)
    dropped.extend(stale_drops)
    merged, duplicate_drops = _merge_duplicate_groups(current)
    dropped.extend(duplicate_drops)
    diversity = select_diverse(
        merged,
        limit=active.max_results,
        mmr_lambda=active.mmr_lambda,
        drop_contradictions=active.drop_contradictions,
    )
    dropped.extend(diversity.dropped_with_reason)
    selected = list(diversity.selected)

    by_type = {memory_type: [candidate for candidate in selected if candidate.memory_type == memory_type] for memory_type in BUDGET_TYPE_ORDER}
    for candidate in selected:
        if candidate.memory_type not in by_type:
            dropped.append(_drop(candidate, "unallocated_memory_type", profile=active.profile))

    blocks: list[ContextBlock] = []
    per_type_spend = {memory_type: 0 for memory_type in BUDGET_TYPE_ORDER}
    for memory_type in BUDGET_TYPE_ORDER:
        allocation = type_budgets[memory_type]
        type_candidates = by_type[memory_type]
        if allocation <= 0:
            dropped.extend(_drop(candidate, "memory_type_budget_is_zero", budget=0) for candidate in type_candidates)
            continue

        viable: list[Candidate] = []
        for candidate in type_candidates:
            one_candidate_text = f"{_type_heading(memory_type)}\n{_candidate_line(candidate)}"
            if estimate(one_candidate_text) > allocation:
                dropped.append(
                    _drop(
                        candidate,
                        "candidate_block_exceeds_type_budget",
                        required_tokens=estimate(one_candidate_text),
                        type_budget=allocation,
                    )
                )
            else:
                viable.append(candidate)

        groups: dict[str, list[Candidate]] = {}
        for candidate in viable:
            groups.setdefault(_evidence_group(candidate), []).append(candidate)

        heading = _type_heading(memory_type)
        block_text = ""
        evidence_groups: list[EvidenceGroup] = []
        block_candidate_ids: list[str] = []
        for group_id in sorted(groups):
            group = groups[group_id]
            lines = "\n".join(_candidate_line(candidate) for candidate in group)
            group_text = f"[evidence: {_compact(group_id)}]\n{lines}"
            prospective = f"{heading}\n\n{group_text}" if not block_text else f"{block_text}\n\n{group_text}"
            prospective_tokens = estimate(prospective)
            if prospective_tokens > allocation:
                dropped.extend(
                    _drop(
                        candidate,
                        "evidence_group_exceeds_remaining_type_budget",
                        evidence_group=group_id,
                        required_block_tokens=prospective_tokens,
                        type_budget=allocation,
                    )
                    for candidate in group
                )
                continue
            block_text = prospective
            ids = tuple(candidate.id for candidate in group)
            block_candidate_ids.extend(ids)
            evidence_groups.append(
                EvidenceGroup(
                    group_id=group_id,
                    candidate_ids=ids,
                    text=group_text,
                    token_count=estimate(group_text),
                )
            )

        if block_text:
            token_count = estimate(block_text)
            per_type_spend[memory_type] = token_count
            blocks.append(
                ContextBlock(
                    memory_type=memory_type,
                    text=block_text,
                    token_count=token_count,
                    candidate_ids=tuple(block_candidate_ids),
                    evidence_groups=tuple(evidence_groups),
                )
            )

    return ComposedContext(
        blocks=tuple(blocks),
        per_type_budget=type_budgets,
        per_type_token_spend=per_type_spend,
        total_tokens=sum(per_type_spend.values()),
        budget=effective_budget,
        dropped_with_reason=tuple(dropped),
    )


__all__ = ["TokenEstimator", "compose_context", "deterministic_token_estimate"]
