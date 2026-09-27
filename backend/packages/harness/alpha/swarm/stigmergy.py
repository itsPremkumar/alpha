"""Stigmergic coordination: decaying shared traces for swarm workers.

Workers that never talk to each other still coordinate through the artefacts
they leave behind.  A trace is a small, bounded record of something a worker
found — a reusable path, a dead end, an artefact, a contradiction — and its
strength encodes how many independent workers corroborated it.

Two properties make this coordination rather than a cache:

* **Evaporation.**  Strength decays exponentially with a half-life, so a hint
  that stops being re-deposited fades instead of becoming permanent gospel.
  Nothing is ever *strengthened* by age.
* **Corroboration amplification.**  ``strength = min(cap, 1 + alpha * (n - 1))``
  for ``n`` independent deposits — deliberately sub-linear, because the second
  witness is worth much less than the first and a linear curve lets three
  identical workers outvote one contradictory expert.

The store is bounded in entries, payload size, and provenance cardinality.  It
is an in-process advisory signal: traces bias ranking and context, never gate a
decision, and no trace is authoritative evidence of anything.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "StigmergicTrace",
    "StigmergicTraceStore",
    "TraceCategory",
]


class TraceCategory(StrEnum):
    WORK = "work"  # a path that worked
    DISCOVERY = "discovery"  # a fact about the workspace/task
    ARTIFACT = "artifact"  # a produced file/handle
    CONFLICT = "conflict"  # a contradiction worth checking
    DEAD_END = "dead_end"  # an approach that did not work


MAX_TRACES = 256
MAX_PAYLOAD_CHARS = 500
MAX_KEY_CHARS = 200
MAX_PROVENANCE = 8
DEFAULT_HALF_LIFE_SECONDS = 3600.0
DEFAULT_AMPLIFICATION_ALPHA = 0.5
DEFAULT_STRENGTH_CAP = 2.0
# Below this, a trace has evaporated into noise and is dropped on sight.
STRENGTH_FLOOR = 0.05


@dataclass
class StigmergicTrace:
    """One corroborated observation left by one or more workers."""

    trace_id: str
    category: str
    key: str
    payload: str = ""
    strength: float = 1.0
    deposits: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    # ``updated_at`` answers "how fresh is this?" and drives eviction/ranking
    # ties.  ``decayed_at`` answers a different question: "as of what moment is
    # ``strength`` correct?".  They must stay separate, because every read path
    # evaporates, so measuring decay from ``updated_at`` without advancing it
    # would re-apply the same window on every read and decay a trace in
    # proportion to how often it was looked at.
    decayed_at: float = 0.0
    provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "category": self.category,
            "key": self.key,
            "payload": self.payload,
            "strength": round(self.strength, 6),
            "deposits": self.deposits,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "decayed_at": self.decayed_at,
            "provenance": list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StigmergicTrace:
        return cls(
            trace_id=str(data.get("trace_id", "")),
            category=str(data.get("category", TraceCategory.WORK.value)),
            key=str(data.get("key", "")),
            payload=str(data.get("payload", ""))[:MAX_PAYLOAD_CHARS],
            strength=max(0.0, float(data.get("strength", 1.0) or 0.0)),
            deposits=max(1, int(data.get("deposits", 1) or 1)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            # A store written before this field existed has no decay epoch;
            # its last refresh is the closest honest approximation.
            decayed_at=float(data.get("decayed_at", data.get("updated_at", 0.0)) or 0.0),
            provenance=[str(item) for item in (data.get("provenance") or []) if item][:MAX_PROVENANCE],
        )


class StigmergicTraceStore:
    """Bounded, evaporation-backed trace store with corroboration amplification."""

    def __init__(
        self,
        *,
        max_traces: int = MAX_TRACES,
        half_life_seconds: float = DEFAULT_HALF_LIFE_SECONDS,
        alpha: float = DEFAULT_AMPLIFICATION_ALPHA,
        strength_cap: float = DEFAULT_STRENGTH_CAP,
    ) -> None:
        if int(max_traces) < 1:
            raise ValueError("max_traces must be at least 1")
        if float(half_life_seconds) <= 0:
            raise ValueError("half_life_seconds must be positive")
        if float(alpha) < 0:
            raise ValueError("alpha must be non-negative")
        if float(strength_cap) < 1.0:
            raise ValueError("strength_cap must be at least 1.0")
        self.max_traces = int(max_traces)
        self.half_life_seconds = float(half_life_seconds)
        self.alpha = float(alpha)
        self.strength_cap = float(strength_cap)
        self._traces: dict[str, StigmergicTrace] = {}

    # -- identity ---------------------------------------------------------

    @staticmethod
    def trace_id_for(category: str, key: str) -> str:
        digest = hashlib.sha256(f"{category}\x00{key}".encode("utf-8")).hexdigest()
        return f"tr-{digest[:16]}"

    @staticmethod
    def _content_key(category: str, key: str) -> str:
        return f"{category}\x00{key}"

    # -- write ------------------------------------------------------------

    def deposit(
        self,
        *,
        category: str | TraceCategory,
        key: str,
        payload: str = "",
        provenance: str = "",
        now: float | None = None,
    ) -> StigmergicTrace:
        """Deposit or corroborate a trace.  Returns the stored trace.

        A repeat of the same ``(category, key)`` from a *different* worker
        corroborates: the sub-linear amplification curve applies and ``deposits``
        increments.  Repeats from the same worker only refresh ``updated_at``,
        so one worker cannot farm its own strength.
        """

        now = float(now if now is not None else time.time())
        category_value = str(getattr(category, "value", category))
        key = str(key or "").strip()[:MAX_KEY_CHARS]
        if not key:
            raise ValueError("trace key must be non-empty")
        payload = str(payload or "")[:MAX_PAYLOAD_CHARS]
        trace_id = self.trace_id_for(category_value, key)
        existing = self._traces.get(trace_id)
        agent = str(provenance or "").strip()[:64]

        if existing is None:
            trace = StigmergicTrace(
                trace_id=trace_id,
                category=category_value,
                key=key,
                payload=payload,
                strength=1.0,
                deposits=1,
                created_at=now,
                updated_at=now,
                decayed_at=now,
                provenance=[agent] if agent else [],
            )
            self._traces[trace_id] = trace
            self._evict_if_needed()
            return trace

        existing.updated_at = now
        # A successful deposit re-states strength as of now — a fresh trace, a
        # corroboration boost, or a same-worker refresh — so the decay epoch
        # moves with it and the next evaporation measures only the new window.
        existing.decayed_at = now
        if payload and not existing.payload:
            existing.payload = payload
        if agent and agent not in existing.provenance:
            existing.provenance.append(agent)
            del existing.provenance[MAX_PROVENANCE:]
            existing.deposits += 1
            existing.strength = min(self.strength_cap, 1.0 + self.alpha * (existing.deposits - 1))
        return existing

    def evict_if_needed(self) -> int:
        return self._evict_if_needed()

    def _evict_if_needed(self) -> int:
        """Bound entry count by dropping the weakest, then the stalest."""

        removed = 0
        while len(self._traces) > self.max_traces:
            victim = min(self._traces.values(), key=lambda trace: (trace.strength, trace.updated_at, trace.trace_id))
            del self._traces[victim.trace_id]
            removed += 1
        return removed

    # -- decay ------------------------------------------------------------

    def evaporate(self, now: float | None = None, *, half_life_seconds: float | None = None) -> int:
        """Apply exponential decay and drop traces that fell below the floor.

        Returns the number of traces removed.  Decay is applied lazily on
        explicit calls and on read paths, so a store that is never touched does
        no work and the result is reproducible under a fixed clock.
        """

        now = float(now if now is not None else time.time())
        half_life = float(half_life_seconds if half_life_seconds is not None else self.half_life_seconds)
        if half_life <= 0:
            raise ValueError("half_life_seconds must be positive")
        removed = 0
        for trace_id, trace in list(self._traces.items()):
            elapsed = max(0.0, now - trace.decayed_at)
            if elapsed <= 0:
                continue
            trace.strength *= 0.5 ** (elapsed / half_life)
            # Advance the epoch, otherwise every later read would re-apply this
            # same window and the trace would decay proportional to read count.
            trace.decayed_at = now
            if trace.strength < STRENGTH_FLOOR:
                del self._traces[trace_id]
                removed += 1
        return removed

    # -- read -------------------------------------------------------------

    def get(self, trace_id: str, *, now: float | None = None) -> StigmergicTrace | None:
        trace = self._traces.get(trace_id)
        if trace is None:
            return None
        if trace.strength < STRENGTH_FLOOR:
            self._traces.pop(trace_id, None)
            return None
        return trace

    def rank(
        self,
        *,
        category: str | TraceCategory | None = None,
        limit: int = 10,
        now: float | None = None,
        min_strength: float = 0.0,
    ) -> list[StigmergicTrace]:
        """Strongest traces first, ties broken by recency then id.

        Deterministic ordering is what makes a ranked trace list usable as a
        stable context slice rather than a reshuffling every read.
        """

        self.evaporate(now)
        category_value = str(getattr(category, "value", category)) if category is not None else None
        matches = [trace for trace in self._traces.values() if (category_value is None or trace.category == category_value) and trace.strength >= float(min_strength)]
        matches.sort(key=lambda trace: (-trace.strength, -trace.updated_at, trace.trace_id))
        return matches[: max(0, int(limit))]

    def count(self, *, category: str | TraceCategory | None = None) -> int:
        if category is None:
            return len(self._traces)
        category_value = str(getattr(category, "value", category))
        return sum(1 for trace in self._traces.values() if trace.category == category_value)

    def clear(self) -> None:
        self._traces.clear()

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "max_traces": self.max_traces,
            "half_life_seconds": self.half_life_seconds,
            "alpha": self.alpha,
            "strength_cap": self.strength_cap,
            "traces": [trace.to_dict() for trace in sorted(self._traces.values(), key=lambda item: item.trace_id)],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> StigmergicTraceStore:
        payload = dict(data) if isinstance(data, Mapping) else {}
        try:
            store = cls(
                max_traces=int(payload.get("max_traces", MAX_TRACES) or MAX_TRACES),
                half_life_seconds=float(payload.get("half_life_seconds", DEFAULT_HALF_LIFE_SECONDS) or DEFAULT_HALF_LIFE_SECONDS),
                alpha=float(payload.get("alpha", DEFAULT_AMPLIFICATION_ALPHA) or DEFAULT_AMPLIFICATION_ALPHA),
                strength_cap=float(payload.get("strength_cap", DEFAULT_STRENGTH_CAP) or DEFAULT_STRENGTH_CAP),
            )
        except (TypeError, ValueError):
            # A malformed store must not take the plan down with it; an empty
            # advisory store is the safe fallback and never a security control.
            return cls()
        for raw in payload.get("traces") or []:
            if not isinstance(raw, Mapping):
                continue
            try:
                trace = StigmergicTrace.from_dict(raw)
            except (TypeError, ValueError):
                continue
            if trace.trace_id and trace.strength >= STRENGTH_FLOOR:
                store._traces[trace.trace_id] = trace
        store._evict_if_needed()
        return store
