"""Pure time-decayed mood aggregation on the valence-arousal circumplex.

Each event receives ``0.5 ** (age_hours / half_life_hours)`` weight. That decay
is multiplied by event intensity and confidence, then normalized. The
``sample_size`` counts events with positive weight, so zero-signal input
returns a confidence-zero state rather than an implied neutral mood.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

from .models import AffectEvent, MoodState, MoodStatus

DEFAULT_HALF_LIFE_HOURS = 72.0
DEFAULT_MOOD_SHIFT_THRESHOLD = 0.35


@dataclass(frozen=True, slots=True)
class MoodShift:
    """Explicit valence/arousal displacement between two mood states."""

    changed: bool
    distance: float
    threshold: float
    valence_delta: float
    arousal_delta: float
    direction: str

    def __bool__(self) -> bool:
        return self.changed


def compute_mood(
    events: Sequence[AffectEvent],
    now: float | None = None,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
) -> MoodState:
    """Compute a deterministic decay-weighted mood at ``now``.

    Events newer than ``now`` are excluded. The function is pure: it performs
    no I/O and does not consult global configuration.
    """
    if not math.isfinite(half_life_hours) or half_life_hours <= 0.0:
        raise ValueError("half_life_hours must be a finite positive number")
    computed_at = time.time() if now is None else float(now)
    weighted: list[tuple[AffectEvent, float]] = []
    for event in events:
        age_hours = (computed_at - event.created_at) / 3600.0
        if age_hours < 0.0:
            continue
        decay = 0.5 ** (age_hours / half_life_hours)
        contribution = decay * event.intensity * event.confidence
        if contribution > 0.0:
            weighted.append((event, contribution))

    if not weighted:
        return MoodState(
            valence=0.0,
            arousal=0.0,
            intensity=0.0,
            confidence=0.0,
            sample_size=0,
            computed_at=computed_at,
            half_life_hours=half_life_hours,
            status=MoodStatus.NO_EVENTS,
        )

    total_weight = math.fsum(weight for _, weight in weighted)
    valence = math.fsum(event.valence * weight for event, weight in weighted) / total_weight
    arousal = math.fsum(event.arousal * weight for event, weight in weighted) / total_weight
    intensity = math.fsum(event.intensity * weight for event, weight in weighted) / total_weight
    confidence = math.fsum(event.confidence * weight for event, weight in weighted) / total_weight
    return MoodState(
        valence=valence,
        arousal=arousal,
        intensity=intensity,
        confidence=confidence,
        sample_size=len(weighted),
        computed_at=computed_at,
        half_life_hours=half_life_hours,
        status=MoodStatus.OK,
    )


def mood_shift(
    previous: MoodState,
    current: MoodState,
    threshold: float = DEFAULT_MOOD_SHIFT_THRESHOLD,
) -> MoodShift:
    """Detect a circumplex shift using Euclidean valence/arousal distance.

    ``threshold`` is explicit and is carried on the result. It is not inferred
    from the event sample, which keeps trajectory alerting deterministic.
    """
    if not math.isfinite(threshold) or threshold < 0.0:
        raise ValueError("threshold must be a finite non-negative number")
    valence_delta = current.valence - previous.valence
    arousal_delta = current.arousal - previous.arousal
    distance = math.hypot(valence_delta, arousal_delta)
    changed = distance >= threshold
    if not changed:
        direction = "stable"
    elif valence_delta > 0.0 and abs(valence_delta) >= abs(arousal_delta):
        direction = "more_positive"
    elif valence_delta < 0.0 and abs(valence_delta) >= abs(arousal_delta):
        direction = "more_negative"
    elif arousal_delta > 0.0:
        direction = "more_activated"
    else:
        direction = "less_activated"
    return MoodShift(
        changed=changed,
        distance=distance,
        threshold=threshold,
        valence_delta=valence_delta,
        arousal_delta=arousal_delta,
        direction=direction,
    )


def mood_trajectory(
    events: Sequence[AffectEvent],
    buckets: int,
    *,
    now: float | None = None,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
) -> list[MoodState]:
    """Bucket events by time and return one mood state per non-empty bucket.

    Buckets are equal-width, chronological, and half-open except for the final
    inclusive edge. Empty buckets are omitted rather than represented by a
    fabricated mood point.
    """
    if isinstance(buckets, bool) or not isinstance(buckets, int) or buckets < 1:
        raise ValueError("buckets must be a positive integer")
    ordered = sorted(events, key=lambda event: (event.created_at, event.id))
    if not ordered:
        return []

    start = ordered[0].created_at
    end = float(now) if now is not None else ordered[-1].created_at
    if end < start:
        end = start
    span = end - start
    if span == 0.0:
        return [compute_mood(ordered, now=end, half_life_hours=half_life_hours)]

    width = span / buckets
    trajectory: list[MoodState] = []
    for index in range(buckets):
        lower = start + index * width
        upper = start + (index + 1) * width
        selected = [event for event in ordered if lower <= event.created_at < upper or (index == buckets - 1 and event.created_at == upper)]
        if not selected:
            continue
        trajectory.append(compute_mood(selected, now=upper, half_life_hours=half_life_hours))
    return trajectory


__all__ = [
    "DEFAULT_HALF_LIFE_HOURS",
    "DEFAULT_MOOD_SHIFT_THRESHOLD",
    "MoodShift",
    "compute_mood",
    "mood_shift",
    "mood_trajectory",
]
