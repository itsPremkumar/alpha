"""MMR selection, near-duplicate suppression, and contradiction supersession."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from alpha.agents.memory.l1.store import tokenize

from .models import Candidate, DroppedItem, FusedCandidate

NEAR_DUPLICATE_THRESHOLD = 0.82
DEFAULT_MMR_LAMBDA = 0.70


class DiversityResult(BaseModel):
    """Selected candidates and every non-selected reason."""

    model_config = ConfigDict(extra="forbid")

    selected: tuple[Candidate, ...] = ()
    dropped_with_reason: tuple[DroppedItem, ...] = Field(default_factory=tuple)


def content_similarity(left: Candidate, right: Candidate) -> float:
    """Token Jaccard similarity using Alpha's shared L1 tokenizer."""
    left_tokens = frozenset(tokenize(left.searchable_text))
    right_tokens = frozenset(tokenize(right.searchable_text))
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _relevance(candidate: Candidate, explicit_scores: Mapping[str, float]) -> float:
    if candidate.id in explicit_scores:
        return float(explicit_scores[candidate.id])
    if isinstance(candidate, FusedCandidate):
        return candidate.fused_score
    return max(candidate.score_components.values(), default=0.0)


def _contradiction_pool(
    candidates: Sequence[Candidate],
    *,
    drop_contradictions: bool,
) -> tuple[list[Candidate], list[DroppedItem]]:
    if not drop_contradictions:
        return list(candidates), []
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.contradiction_group:
            groups.setdefault(candidate.contradiction_group, []).append(candidate)

    superseded_ids: set[str] = set()
    dropped: list[DroppedItem] = []
    winners: dict[str, Candidate] = {}
    for group_name, group in groups.items():
        ids = {candidate.id for candidate in group}
        ordered = sorted(
            group,
            key=lambda candidate: (
                -int(any(target in ids for target in candidate.supersedes)),
                -candidate.authority,
                -candidate.recency,
                -float(candidate.score_components.get("confidence", 0.0)),
                candidate.id,
            ),
        )
        winner = ordered[0]
        winners[group_name] = winner
        for loser in ordered[1:]:
            superseded_ids.add(loser.id)
            dropped.append(
                DroppedItem(
                    candidate_id=loser.id,
                    reason="contradiction_superseded",
                    details={"kept_id": winner.id, "contradiction_group": group_name},
                )
            )
    return [candidate for candidate in candidates if candidate.id not in superseded_ids], dropped


def _suppress_near_duplicates(
    candidates: Sequence[Candidate],
    scores: Mapping[str, float],
) -> tuple[list[Candidate], list[DroppedItem]]:
    kept: list[Candidate] = []
    dropped: list[DroppedItem] = []
    ordered = sorted(candidates, key=lambda candidate: (-_relevance(candidate, scores), candidate.id))
    for candidate in ordered:
        duplicate_of: tuple[Candidate, float] | None = None
        for representative in kept:
            if candidate.contradiction_group and candidate.contradiction_group == representative.contradiction_group:
                continue
            similarity = content_similarity(candidate, representative)
            if similarity >= NEAR_DUPLICATE_THRESHOLD and (duplicate_of is None or similarity > duplicate_of[1]):
                duplicate_of = (representative, similarity)
        if duplicate_of is None:
            kept.append(candidate)
            continue
        representative, similarity = duplicate_of
        dropped.append(
            DroppedItem(
                candidate_id=candidate.id,
                reason="near_duplicate",
                details={"kept_id": representative.id, "similarity": similarity},
            )
        )
    return kept, dropped


def select_diverse(
    candidates: Sequence[Candidate],
    *,
    limit: int,
    mmr_lambda: float = DEFAULT_MMR_LAMBDA,
    drop_contradictions: bool = True,
    relevance_scores: Mapping[str, float] | None = None,
) -> DiversityResult:
    """Select candidates using ``lambda * relevance - (1-lambda) * similarity``.

    The default lambda is 0.70. Contradictions are resolved before diversity:
    the candidate with an explicit supersession link, then higher authority,
    then newer effective timestamp, wins. Token Jaccard >= 0.82 suppresses a
    lower-ranked near duplicate before MMR. Final MMR ties use ascending id.
    """
    if not 0.0 <= mmr_lambda <= 1.0:
        raise ValueError("mmr_lambda must be between 0 and 1")
    if limit < 0:
        raise ValueError("limit must be non-negative")
    scores = dict(relevance_scores or {})
    contradiction_pool, contradiction_drops = _contradiction_pool(candidates, drop_contradictions=drop_contradictions)
    unique_pool, duplicate_drops = _suppress_near_duplicates(contradiction_pool, scores)
    remaining = sorted(unique_pool, key=lambda candidate: candidate.id)
    selected: list[Candidate] = []

    while remaining and len(selected) < limit:
        best: Candidate | None = None
        best_value = float("-inf")
        for candidate in remaining:
            redundancy = max((content_similarity(candidate, chosen) for chosen in selected), default=0.0)
            mmr_value = mmr_lambda * _relevance(candidate, scores) - (1.0 - mmr_lambda) * redundancy
            if mmr_value > best_value:
                best = candidate
                best_value = mmr_value
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)

    limit_drops = [
        DroppedItem(candidate_id=candidate.id, reason="mmr_limit", details={"limit": limit})
        for candidate in remaining
    ]
    return DiversityResult(
        selected=tuple(selected),
        dropped_with_reason=tuple([*contradiction_drops, *duplicate_drops, *limit_drops]),
    )


__all__ = [
    "DEFAULT_MMR_LAMBDA",
    "DiversityResult",
    "NEAR_DUPLICATE_THRESHOLD",
    "content_similarity",
    "select_diverse",
]
