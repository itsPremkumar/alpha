"""Mood-aware recall selection, bounded prompt rendering, and score boosting."""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from alpha.agents.memory.l1.store import tokenize

from .models import AffectEvent, MoodState

if TYPE_CHECKING:
    from .memory import AffectiveMemory


def _query_rank(event: AffectEvent, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    event_tokens = set(tokenize(event.content))
    return len(query_tokens & event_tokens) / len(query_tokens)


def select_recent_moments(
    events: Sequence[AffectEvent],
    *,
    top_k: int,
    query: str | None = None,
    min_confidence: float,
    agent_name: str | None = None,
) -> list[AffectEvent]:
    """Select a bounded, deterministic emotional-moment slice."""
    if top_k < 1:
        return []
    query_tokens = set(tokenize(query or ""))
    eligible = [event for event in events if event.confidence >= min_confidence and (agent_name is None or event.agent_name == agent_name)]
    if query_tokens:
        ranked = [(_query_rank(event, query_tokens), event.intensity * event.confidence, event) for event in eligible]
        ranked = [item for item in ranked if item[0] > 0.0]
        ranked.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -item[2].created_at,
                item[2].id,
            )
        )
        return [item[2] for item in ranked[:top_k]]

    ordered = sorted(
        eligible,
        key=lambda event: (
            -event.created_at,
            -event.intensity,
            -event.confidence,
            event.id,
        ),
    )
    return ordered[:top_k]


def recent_moments(
    memory: AffectiveMemory,
    user_id: str,
    top_k: int | None = None,
    query: str | None = None,
    *,
    agent_name: str | None = None,
) -> list[AffectEvent]:
    """Return recent moments through an injected affective-memory instance."""
    return memory.recent_moments(
        user_id,
        top_k=top_k,
        query=query,
        agent_name=agent_name,
    )


def mood_state(
    memory: AffectiveMemory,
    user_id: str,
    now: float | None = None,
) -> MoodState | None:
    """Return a store-backed mood or ``None`` when no reliable state exists."""
    return memory.mood_state(user_id, now=now)


def _single_line(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    return html.escape(compact[:limit], quote=True)


def render_block(
    memory: AffectiveMemory,
    user_id: str,
    *,
    top_k: int | None = None,
    query: str | None = None,
    agent_name: str | None = None,
    now: float | None = None,
) -> str:
    """Render a bounded, escaped prompt block with honest unavailable states."""
    if not memory.enabled:
        return ""
    status = memory.store.read_status(user_id)
    if status != "ok":
        return f"### Affective memory\n- Unavailable: {status}; no mood was inferred."

    limit = memory.config.max_surfaced if top_k is None else min(top_k, memory.config.max_surfaced)
    moments = memory.recent_moments(
        user_id,
        top_k=max(0, limit),
        query=query,
        agent_name=agent_name,
    )
    state = memory.mood_state(user_id, now=now)
    lines = ["### Affective memory"]
    if state is not None:
        lines.append(f"- Current mood: valence {state.valence:+.3f}, arousal {state.arousal:.3f}, intensity {state.intensity:.3f} (n={state.sample_size}, confidence={state.confidence:.3f}).")
    if not moments:
        lines.append("- No reliable affective moments are available yet.")
        return "\n".join(lines)

    for event in moments:
        lines.append(f"- [{event.source.value}] v={event.valence:+.2f} a={event.arousal:.2f} i={event.intensity:.2f}: {_single_line(event.content)}")
    return "\n".join(lines)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _mapping_value(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _score_context(item: Any) -> tuple[float, float, float]:
    """Read a base score and optional valence/arousal from common scored shapes."""
    value = item
    score_value: Any = None
    metadata: Mapping[str, Any] = {}
    if isinstance(item, tuple) and len(item) == 2:
        value, score_value = item
    if isinstance(value, Mapping):
        score_value = _mapping_value(value, "score", "composite_score", default=score_value)
        raw_metadata = _mapping_value(value, "metadata", default={})
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        metadata = value if not metadata else metadata
    else:
        score_value = score_value if score_value is not None else getattr(value, "score", None)
        if score_value is None:
            score_value = getattr(value, "composite_score", None)
        raw_metadata = getattr(value, "metadata", None)
        if isinstance(raw_metadata, Mapping):
            metadata = raw_metadata
        if isinstance(value, AffectEvent):
            metadata = value

    if score_value is None:
        raise ValueError("each scored item must expose score or composite_score")
    nested = metadata.get("affect") if isinstance(metadata, Mapping) else None
    affect = nested if isinstance(nested, Mapping) else metadata
    valence = _number(_mapping_value(affect, "valence", default=0.0))
    arousal = _number(_mapping_value(affect, "arousal", default=0.0))
    return _number(score_value), min(1.0, max(-1.0, valence)), min(1.0, max(0.0, arousal))


def mood_boost[ScoredItemT](
    scored_items: Sequence[ScoredItemT],
    mood: MoodState | None,
    weight: float = 0.15,
) -> list[tuple[ScoredItemT, float]]:
    """Return deterministically re-ranked ``(item, adjusted_score)`` pairs.

    Items may expose ``score``/``composite_score`` and a ``metadata`` mapping
    with optional ``valence`` and ``arousal`` values. The additive correction is
    ``weight * mood.confidence * dot(item_affect, mood) / 2`` and is therefore
    bounded by ``weight`` in either direction. ``weight`` itself is clamped to
    ``[0, 1]``. Inputs are never mutated.

    Tier-2 integration should call this after L1 hybrid relevance is computed,
    use the returned scores to order candidates, and only then take ``top_k``.
    A missing mood or a confidence-zero mood produces no score correction.
    """
    effective_weight = min(1.0, max(0.0, _number(weight)))
    boosted: list[tuple[int, ScoredItemT, float]] = []
    for index, item in enumerate(scored_items):
        base, valence, arousal = _score_context(item)
        correction = 0.0
        if mood is not None and mood.sample_size > 0 and mood.confidence > 0.0:
            alignment = (valence * mood.valence + arousal * mood.arousal) / 2.0
            correction = effective_weight * mood.confidence * alignment
        boosted.append((index, item, base + correction))

    boosted.sort(key=lambda entry: (-entry[2], entry[0]))
    return [(item, score) for _, item, score in boosted]


__all__ = [
    "mood_boost",
    "mood_state",
    "recent_moments",
    "render_block",
    "select_recent_moments",
]
