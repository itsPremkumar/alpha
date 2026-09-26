"""Similarity-driven merge proposals; this module never performs a merge.

The host owns the underlying memory records and all authorization around a
merge.  This policy only groups records whose *injected* similarity meets a
threshold, then chooses a survivor deterministically by utility, age, and id.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .models import DedupSuggestion, UtilityRecord

Similarity = Callable[[UtilityRecord, UtilityRecord], float] | Mapping[Any, Any] | None


def _record(value: UtilityRecord | Mapping[str, Any]) -> UtilityRecord:
    if isinstance(value, UtilityRecord):
        return value
    return UtilityRecord.model_validate(dict(value))


def _lookup_similarity(similarity: Mapping[Any, Any], left: str, right: str) -> float | None:
    if left in similarity:
        nested = similarity[left]
        if isinstance(nested, Mapping):
            value = nested.get(right)
            if value is None:
                reverse = similarity.get(right)
                if isinstance(reverse, Mapping):
                    value = reverse.get(left)
        else:
            value = None
    else:
        value = similarity.get((left, right))
        if value is None:
            value = similarity.get((right, left))
        if value is None:
            value = similarity.get(f"{left}|{right}")
        if value is None:
            value = similarity.get(f"{right}|{left}")
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _pair_similarity(similarity: Similarity, left: UtilityRecord, right: UtilityRecord) -> float | None:
    if callable(similarity):
        try:
            raw = similarity(left, right)
        except (TypeError, ValueError, RuntimeError):
            return None
    elif isinstance(similarity, Mapping):
        raw = _lookup_similarity(similarity, left.record_id, right.record_id)
    else:
        return None
    if raw is None:
        return None
    try:
        result = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self._parent[right_root] = left_root
        else:
            self._parent[left_root] = right_root


def _survivor(records: list[UtilityRecord]) -> UtilityRecord:
    # Higher utility wins; for equal utility the older record wins, then id.
    return sorted(records, key=lambda item: (-item.score, item.first_seen, item.record_id))[0]


def suggest_dedup(
    records: Iterable[UtilityRecord | Mapping[str, Any]],
    *,
    similarity: Similarity,
    threshold: float = 0.85,
) -> list[DedupSuggestion]:
    """Return connected duplicate groups and a deterministic survivor."""

    try:
        cutoff = float(threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("similarity threshold must be numeric") from exc
    if not math.isfinite(cutoff) or not 0.0 <= cutoff <= 1.0:
        raise ValueError("similarity threshold must be between 0 and 1")
    parsed = sorted((_record(item) for item in records), key=lambda item: item.record_id)
    if len(parsed) < 2 or similarity is None:
        return []

    union = _UnionFind(item.record_id for item in parsed)
    for index, left in enumerate(parsed):
        for right in parsed[index + 1 :]:
            pair_score = _pair_similarity(similarity, left, right)
            if pair_score is not None and pair_score >= cutoff:
                union.union(left.record_id, right.record_id)

    groups: dict[str, list[UtilityRecord]] = {}
    for record in parsed:
        groups.setdefault(union.find(record.record_id), []).append(record)
    suggestions: list[DedupSuggestion] = []
    for group in sorted(groups.values(), key=lambda items: items[0].record_id):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: item.record_id)
        survivor = _survivor(ordered)
        oldest = min(item.first_seen for item in ordered)
        newest = max(item.first_seen for item in ordered)
        why = f"similarity >= {cutoff:.6f} for connected group; survivor chosen by utility descending, age descending (first_seen={oldest:.6f}..{newest:.6f}), then id; chosen_utility={survivor.score:.6f}; no records removed"
        suggestions.append(
            DedupSuggestion(
                record_ids=[item.record_id for item in ordered],
                suggested_survivor=survivor.record_id,
                why=why,
                merged_utility=max(item.score for item in ordered),
            )
        )
    return suggestions


def dedup_suggestions(
    records: Iterable[UtilityRecord | Mapping[str, Any]],
    *,
    similarity: Similarity,
    threshold: float = 0.85,
) -> list[DedupSuggestion]:
    return suggest_dedup(records, similarity=similarity, threshold=threshold)


class DedupPolicy:
    """Reusable proposal-only dedup policy."""

    def __init__(self, *, similarity: Similarity, threshold: float = 0.85) -> None:
        self.similarity = similarity
        self.threshold = threshold

    def suggest(
        self,
        records: Iterable[UtilityRecord | Mapping[str, Any]],
        *,
        threshold: float | None = None,
    ) -> list[DedupSuggestion]:
        return suggest_dedup(
            records,
            similarity=self.similarity,
            threshold=self.threshold if threshold is None else threshold,
        )

    def suggestions(
        self,
        records: Iterable[UtilityRecord | Mapping[str, Any]],
        *,
        threshold: float | None = None,
    ) -> list[DedupSuggestion]:
        return self.suggest(records, threshold=threshold)


def propose_dedup(
    records: Iterable[UtilityRecord | Mapping[str, Any]],
    *,
    similarity: Similarity,
    threshold: float = 0.85,
) -> list[DedupSuggestion]:
    return suggest_dedup(records, similarity=similarity, threshold=threshold)


dedup = suggest_dedup


__all__ = [
    "dedup",
    "DedupPolicy",
    "Similarity",
    "dedup_suggestions",
    "propose_dedup",
    "suggest_dedup",
]
