"""Spatio-Temporal Memory Engine.

Tracks state changes, point-in-time facts, environment execution context,
and temporal interval validity (inspired by Graphiti & Letta).
"""

from __future__ import annotations

import time
from typing import Any

from agent_workspace.memory.cognitive.models import SpatioTemporalEvent


class SpatioTemporalMemory:
    """Timeline and environmental context index supporting time-travel and spatial queries."""

    def __init__(self, max_events: int = 1000) -> None:
        self.max_events = max_events
        self._events: dict[str, SpatioTemporalEvent] = {}

    def record_event(
        self,
        title: str,
        description: str,
        timestamp: float | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
        environment: str = "local",
        location: str = "workspace",
        host: str = "localhost",
        entities: list[str] | None = None,
        event_id: str | None = None,
    ) -> SpatioTemporalEvent:
        """Record a spatio-temporal event with explicit interval validity."""
        now = time.time()
        evt = SpatioTemporalEvent(
            title=title.strip(),
            description=description.strip(),
            timestamp=timestamp or now,
            valid_from=valid_from or (timestamp or now),
            valid_to=valid_to,
            environment=environment.strip(),
            location=location.strip(),
            host=host.strip(),
            entities=entities or [],
        )
        if event_id:
            evt.event_id = event_id
        self._events[evt.event_id] = evt
        self._enforce_capacity()
        return evt

    def query_at_time(self, target_time: float, environment: str | None = None) -> list[SpatioTemporalEvent]:
        """Time-travel query: Find events/facts that were valid at point-in-time `target_time`."""
        results = []
        for evt in self._events.values():
            if evt.valid_from <= target_time and (evt.valid_to is None or target_time <= evt.valid_to):
                if environment is None or evt.environment.lower() == environment.lower():
                    results.append(evt)
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results

    def query_by_interval(self, start_time: float, end_time: float) -> list[SpatioTemporalEvent]:
        """Find events whose validity intervals overlap with [start_time, end_time]."""
        if start_time > end_time:
            return []
        results = []
        for evt in self._events.values():
            evt_end = evt.valid_to if evt.valid_to is not None else float("inf")
            # Overlap condition: max(start1, start2) <= min(end1, end2)
            if max(start_time, evt.valid_from) <= min(end_time, evt_end):
                results.append(evt)
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results

    def list_events(self, limit: int = 50) -> list[SpatioTemporalEvent]:
        evts = list(self._events.values())
        evts.sort(key=lambda e: e.timestamp, reverse=True)
        return evts[:limit]

    def _enforce_capacity(self) -> None:
        if len(self._events) <= self.max_events:
            return
        sorted_keys = sorted(self._events.keys(), key=lambda k: self._events[k].timestamp)
        excess = len(self._events) - self.max_events
        for k in sorted_keys[:excess]:
            del self._events[k]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": len(self._events),
            "events": [e.to_dict() for e in self.list_events(limit=50)],
        }
