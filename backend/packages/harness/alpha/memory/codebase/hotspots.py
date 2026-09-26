"""Heuristic churn/centrality hotspot scoring.

The scorer consumes signals supplied by the host; it never shells out to Git
or invents a history signal.  For a path with available signals::

    churn = normalized(0.5 * recency_weighted_commit_count + edit_count)
    score = 0.5 * churn + 0.5 * normalized_fan_in

``recency_weighted_commit_count`` is ``sum(0.5 ** (age_days / 30))`` using the
injected reference time, and normalization is by the maximum value in the
current path set.  If one signal family is missing, its component is ``None``,
the available component is used with its disclosed weight, and ``partial`` is
true.  If both are missing, ``score`` is ``None``.  Thus a missing input can
never masquerade as a cold file.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence

from .graph import DependencyGraph
from .models import Hotspot

Signal = Mapping[str, object] | Callable[[str], object] | None


def _values_for(path: str, signal: Signal) -> object | None:
    if signal is None:
        return None
    if callable(signal):
        return signal(path)
    return signal.get(path)


def _timestamps_for(path: str, signal: Signal) -> list[float] | None:
    value = _values_for(path, signal)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        try:
            return [float(value)]
        except ValueError:
            return None
    if isinstance(value, Sequence):
        result: list[float] = []
        for item in value:
            try:
                result.append(float(item))
            except (TypeError, ValueError):
                continue
        return result
    return None


def _number_for(path: str, signal: Signal) -> float | None:
    value = _values_for(path, signal)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def score_hotspots(
    paths: Iterable[str],
    *,
    commit_timestamps: Signal = None,
    edit_counts: Signal = None,
    fan_in: Signal | DependencyGraph | None = None,
    now: float | None = None,
    churn_weight: float = 0.5,
    fan_in_weight: float = 0.5,
) -> list[Hotspot]:
    """Return deterministic, explicitly heuristic hotspot records.

    Args:
        paths: Candidate indexed paths.  Duplicate paths are removed.
        commit_timestamps: Mapping/callable returning timestamps per path.
        edit_counts: Mapping/callable returning edit counts per path.
        fan_in: Mapping/callable fan-in values or a :class:`DependencyGraph`.
        now: Injected Unix timestamp used for recency decay.
        churn_weight/fan_in_weight: Non-negative component weights.  At least
            one must be positive; their values are disclosed in each record.
    """

    if churn_weight < 0.0 or fan_in_weight < 0.0 or churn_weight + fan_in_weight <= 0.0:
        raise ValueError("hotspot weights must be non-negative with a positive sum")
    reference = float(now) if now is not None else time.time()
    candidates = sorted({str(path) for path in paths if str(path)})
    if not candidates:
        return []
    graph_fan_in = fan_in.fan_in() if isinstance(fan_in, DependencyGraph) else None
    timestamp_map = {path: _timestamps_for(path, commit_timestamps) for path in candidates}
    edit_map = {path: _number_for(path, edit_counts) for path in candidates}
    fan_map = {path: (graph_fan_in.get(path) if graph_fan_in is not None else _number_for(path, fan_in)) for path in candidates}

    raw_churn: dict[str, float] = {}
    for path in candidates:
        timestamps = timestamp_map[path]
        edits = edit_map[path]
        if timestamps is None and edits is None:
            continue
        recency = 0.0 if timestamps is None else sum(0.5 ** (max(0.0, reference - stamp) / (30.0 * 86400.0)) for stamp in timestamps)
        edit_value = 0.0 if edits is None else max(0.0, edits)
        # The two available churn inputs are averaged rather than allowing a
        # large history count to erase a separate edit signal.
        components: list[float] = []
        if timestamps is not None:
            components.append(recency)
        if edits is not None:
            components.append(edit_value)
        raw_churn[path] = sum(components) / len(components) if components else 0.0
    churn_max = max(raw_churn.values(), default=0.0)
    normalized_churn = {path: (value / churn_max if churn_max > 0.0 else 0.0) for path, value in raw_churn.items()}

    numeric_fan_in = {path: value for path, value in fan_map.items() if value is not None}
    fan_max = max(numeric_fan_in.values(), default=0.0)
    normalized_fan = {path: (max(0.0, value) / fan_max if fan_max > 0.0 else 0.0) for path, value in numeric_fan_in.items()}
    results: list[Hotspot] = []
    for path in candidates:
        missing: list[str] = []
        disclosures: list[str] = []
        churn_value = normalized_churn.get(path)
        if commit_timestamps is None and edit_counts is None:
            missing.append("churn")
            disclosures.append("missing churn signal: commit_timestamps/edit_counts")
        elif churn_value is None:
            missing.append("churn")
            disclosures.append("missing churn signal for path")
        if fan_in is None:
            missing.append("fan_in")
            disclosures.append("missing fan_in signal")
        elif path not in normalized_fan:
            missing.append("fan_in")
            disclosures.append("missing fan_in signal for path")
        fan_value = normalized_fan.get(path)
        available_weight = 0.0
        weighted = 0.0
        if churn_value is not None:
            available_weight += churn_weight
            weighted += churn_weight * churn_value
        if fan_value is not None:
            available_weight += fan_in_weight
            weighted += fan_in_weight * fan_value
        score = weighted / available_weight if available_weight > 0.0 else None
        inputs: dict[str, object] = {
            "churn_weight": churn_weight,
            "fan_in_weight": fan_in_weight,
            "commit_timestamps": timestamp_map[path],
            "edit_count": edit_map[path],
            "fan_in": fan_map[path],
        }
        if score is not None:
            inputs["formula"] = "0.5*churn+0.5*fan_in (available components renormalized)"
        if score is not None and missing:
            disclosures.append("partial heuristic score: one or more signal families unavailable")
        results.append(
            Hotspot(
                path=path,
                churn_score=churn_value,
                fan_in_score=fan_value,
                score=score,
                inputs=inputs,
                missing_inputs=missing,
                disclosures=disclosures,
                heuristic=True,
            )
        )
    results.sort(key=lambda item: (-(item.score if item.score is not None else -1.0), item.path))
    return results


class HotspotScorer:
    """Small injectable wrapper around :func:`score_hotspots`."""

    def __init__(self, *, now: float | None = None, churn_weight: float = 0.5, fan_in_weight: float = 0.5) -> None:
        self.now = now
        self.churn_weight = churn_weight
        self.fan_in_weight = fan_in_weight

    def score(
        self,
        paths: Iterable[str],
        *,
        commit_timestamps: Signal = None,
        edit_counts: Signal = None,
        fan_in: Signal | DependencyGraph | None = None,
    ) -> list[Hotspot]:
        return score_hotspots(
            paths,
            commit_timestamps=commit_timestamps,
            edit_counts=edit_counts,
            fan_in=fan_in,
            now=self.now,
            churn_weight=self.churn_weight,
            fan_in_weight=self.fan_in_weight,
        )


def compute_hotspots(
    paths: Iterable[str],
    *,
    commit_timestamps: Signal = None,
    edit_counts: Signal = None,
    fan_in: Signal | DependencyGraph | None = None,
    now: float | None = None,
) -> list[Hotspot]:
    """Alias for :func:`score_hotspots` used by refresh hosts."""

    return score_hotspots(paths, commit_timestamps=commit_timestamps, edit_counts=edit_counts, fan_in=fan_in, now=now)


def calculate_hotspots(
    paths: Iterable[str],
    *,
    commit_timestamps: Signal = None,
    edit_counts: Signal = None,
    fan_in: Signal | DependencyGraph | None = None,
    now: float | None = None,
) -> list[Hotspot]:
    """Compatibility alias with a descriptive function name."""

    return score_hotspots(paths, commit_timestamps=commit_timestamps, edit_counts=edit_counts, fan_in=fan_in, now=now)


__all__ = ["HotspotScorer", "calculate_hotspots", "compute_hotspots", "score_hotspots"]
