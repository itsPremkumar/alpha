"""Timeline and moment-selection views over narrative source events."""

from __future__ import annotations

import re
from typing import Any

from .config import NarrativeConfig
from .models import NarrativeEvent, _timestamp
from .store import NarrativeStore
from .synthesis import _group_events, choose_bucket, deterministic_entry

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(value or "")}


def _query_score(event: NarrativeEvent, query: str | None) -> float:
    if not query or not query.strip():
        return float(event.importance)
    terms = _tokens(query)
    if not terms:
        return float(event.importance)
    text = " ".join((event.title, event.summary, *event.participants, *event.outcomes)).casefold()
    matches = sum(1 for term in terms if term in text)
    return matches * 1_000.0 + event.importance


class NarrativeTimeline:
    """Read-only temporal projections for one narrative store."""

    def __init__(self, store: NarrativeStore, config: NarrativeConfig | None = None) -> None:
        self.store = store
        self.config = config if config is not None else store.config

    def timeline(
        self,
        scope: Any = "user",
        start: float | None = None,
        end: float | None = None,
        *,
        scope_id: str | None = None,
    ) -> list[NarrativeEvent]:
        """Return events overlapping the requested half-open range."""

        start_value = None if start is None else _timestamp(start, "start")
        end_value = None if end is None else _timestamp(end, "end")
        return self.store.list_events(scope, start_value, end_value, scope_id=scope_id)

    def moments(
        self,
        scope: Any = "user",
        top_k: int = 5,
        query: str | None = None,
        *,
        scope_id: str | None = None,
    ) -> list[NarrativeEvent]:
        """Return the most relevant bounded moments, with recency tie-breaking."""

        limit = max(0, int(top_k))
        if limit == 0:
            return []
        events = self.store.list_events(scope, scope_id=scope_id)
        terms = _tokens(query or "")
        if terms:
            events = [event for event in events if any(term in " ".join((event.title, event.summary, *event.participants, *event.outcomes)).casefold() for term in terms)]
        ranked = sorted(
            events,
            key=lambda event: (
                -_query_score(event, query),
                -event.period_start,
                -event.importance,
                event.id,
            ),
        )
        return ranked[:limit]

    def periods(self, scope: Any = "user", *, scope_id: str | None = None) -> list[str]:
        """Return unique chronological period labels for a scope's events."""

        events = self.store.list_events(scope, scope_id=scope_id)
        if not events:
            return []
        bucket = choose_bucket(events)
        _, groups = _group_events(events, bucket)
        return sorted(groups, key=lambda period: min(event.period_start for event in groups[period]))

    def range_summary(
        self,
        scope: Any = "user",
        start: float | None = None,
        end: float | None = None,
        *,
        scope_id: str | None = None,
    ) -> str:
        """Summarize a date range without inventing events outside it."""

        events = self.timeline(scope, start, end, scope_id=scope_id)
        if not events:
            return ""
        lines = [deterministic_entry(event) for event in events]
        summary = "\n".join(lines)
        if len(summary) <= self.config.max_chars:
            return summary
        marker = "\n[disclosure: range summary truncated]"
        if self.config.max_chars <= len(marker):
            return summary[: self.config.max_chars]
        return summary[: self.config.max_chars - len(marker)].rstrip() + marker


def _store_from(owner: Any) -> NarrativeStore:
    store = getattr(owner, "store", owner)
    if not isinstance(store, NarrativeStore):
        raise TypeError("timeline owner must expose a NarrativeStore")
    return store


def timeline(
    owner: Any,
    scope: Any = "user",
    start: float | None = None,
    end: float | None = None,
    *,
    scope_id: str | None = None,
) -> list[NarrativeEvent]:
    """Module-level convenience wrapper around :class:`NarrativeTimeline`."""

    return NarrativeTimeline(_store_from(owner)).timeline(scope, start, end, scope_id=scope_id)


def moments(
    owner: Any,
    scope: Any = "user",
    top_k: int = 5,
    query: str | None = None,
    *,
    scope_id: str | None = None,
) -> list[NarrativeEvent]:
    """Module-level convenience wrapper for bounded moment selection."""

    return NarrativeTimeline(_store_from(owner)).moments(scope, top_k, query, scope_id=scope_id)


def periods(owner: Any, scope: Any = "user", *, scope_id: str | None = None) -> list[str]:
    """Module-level convenience wrapper for period labels."""

    return NarrativeTimeline(_store_from(owner)).periods(scope, scope_id=scope_id)


def range_summary(
    owner: Any,
    scope: Any = "user",
    start: float | None = None,
    end: float | None = None,
    *,
    scope_id: str | None = None,
) -> str:
    """Module-level convenience wrapper for an event-range summary."""

    return NarrativeTimeline(_store_from(owner)).range_summary(scope, start, end, scope_id=scope_id)


__all__ = [
    "NarrativeTimeline",
    "moments",
    "periods",
    "range_summary",
    "timeline",
]
