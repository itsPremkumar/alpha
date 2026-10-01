"""Bounded, per-catalog-session telemetry for the tool discovery layer.

The counters live on the catalog snapshot's session, never in a process-global
singleton: two runs in one process get independent counters, and a test can
assert that by building two sessions side by side.

Scope semantics follow the published behavioral contract:

* ``catalog_size`` and ``sources`` describe the resolved, policy-filtered
  catalog, not the raw registry.
* ``counter_scope`` is stable while tools are *appended* or prompt policy
  narrows the catalog, and changes when the catalog is *replaced* or restored.
  The scope is therefore derived from the identity of the entries that were
  present at session start, not from the live size.
* ``search_count`` / ``describe_count`` / ``call_count`` are running totals for
  the catalog session, carried across calls rather than reset per call.

Boundedness: counters are plain integers capped at ``COUNTER_MAX`` so a
runaway caller cannot grow them without limit, and activity records are a
bounded deque (``ACTIVITY_MAX``) of redacted one-line summaries. No synthetic
model turns are produced.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: Upper bound on any running counter. Reaching it means "at least this many",
#: which is the honest reading; a counter never wraps back to a small number.
COUNTER_MAX = 1_000_000

#: Bounded per-session activity tail (persisted as display activity).
ACTIVITY_MAX = 200

#: Longest retained activity line, in characters.
ACTIVITY_CHAR_MAX = 240

#: Truncation marker appended to a clipped activity line.
CLIP_MARKER = "..."


def _clip(text: str) -> str:
    if len(text) <= ACTIVITY_CHAR_MAX:
        return text
    return text[: ACTIVITY_CHAR_MAX - len(CLIP_MARKER)] + CLIP_MARKER


@dataclass
class DiscoveryTelemetry:
    """Running counters for one catalog session."""

    catalog_size: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    counter_scope: str = ""
    search_count: int = 0
    describe_count: int = 0
    call_count: int = 0
    blocked_count: int = 0
    activity: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=ACTIVITY_MAX))

    @classmethod
    def for_catalog(cls, snapshot: Any, *, scope: str | None = None) -> DiscoveryTelemetry:
        """Build telemetry describing *snapshot*'s size, source split, and scope.

        ``scope`` is the owning session's counter scope. Passing it is how the
        scope stays stable while the catalog grows or prompt policy narrows
        it; omitting it derives a fresh scope, which is what a *replaced*
        catalog gets.
        """
        resolved_scope = scope or compute_counter_scope(snapshot.entry_ids)
        return cls(
            catalog_size=snapshot.size,
            sources=dict(snapshot.source_counts),
            counter_scope=resolved_scope,
        )

    def record_search(self, *, queries: int, candidates: int, truncated: bool) -> None:
        """Count one search operation (a batch counts once)."""
        self.search_count = min(COUNTER_MAX, self.search_count + 1)
        self._append("search", f"queries={queries} candidates={candidates} truncated={str(truncated).lower()}")

    def record_describe(self, *, entry_id: str, ok: bool) -> None:
        """Count one describe operation."""
        self.describe_count = min(COUNTER_MAX, self.describe_count + 1)
        self._append("describe", f"id={entry_id} ok={str(ok).lower()}")

    def record_call(self, *, entry_id: str, outcome: str, duration_ms: float | None = None) -> None:
        """Count one call operation and its disclosed outcome."""
        self.call_count = min(COUNTER_MAX, self.call_count + 1)
        if outcome not in ("ok", "executed"):
            self.blocked_count = min(COUNTER_MAX, self.blocked_count + 1)
        detail = f"id={entry_id} outcome={outcome}"
        if duration_ms is not None:
            detail += f" duration_ms={duration_ms:.0f}"
        self._append("call", detail)

    def to_dict(self) -> dict[str, Any]:
        """Serializable projection attached to telemetry-bearing results."""
        return {
            "catalogSize": self.catalog_size,
            "sources": dict(sorted(self.sources.items())),
            "counterScope": self.counter_scope,
            "searchCount": self.search_count,
            "describeCount": self.describe_count,
            "callCount": self.call_count,
            "blockedCount": self.blocked_count,
        }

    def activity_lines(self) -> list[str]:
        """Bounded, redacted display activity for session history."""
        return [f"{item['op']} {item['detail']}".strip() for item in self.activity]

    def to_json(self) -> str:
        """Stable JSON rendering, used by the payload regression test."""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def _append(self, op: str, detail: str) -> None:
        self.activity.append({"op": op, "detail": _clip(detail)})


def compute_counter_scope(entry_ids: tuple[str, ...]) -> str:
    """Derive a counter scope id from a set of catalog entry ids.

    Pure derivation so the snapshot, the session, and the tests agree on the
    same function. The *stability* guarantee is the session's job: a session
    creates its scope once and keeps passing it forward, so appending tools or
    narrowing policy does not disturb the counters. A replaced catalog gets a
    new session and therefore a new scope.
    """
    canonical = json.dumps(sorted(entry_ids), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
