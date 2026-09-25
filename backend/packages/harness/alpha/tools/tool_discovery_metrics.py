"""Bounded, redacted per-session telemetry for deferred tool discovery.

The store is process-memory-only. It retains at most ``MAX_SESSIONS`` thread
sessions for ``RETENTION_SECONDS`` after their last recorded operation, and
never stores query text, tool names, arguments, results, or raw session IDs.
Session IDs are immediately reduced to a 16-hex-character SHA-256 prefix.
Counter values saturate at ``MAX_COUNTER_VALUE`` so repeated operations cannot
grow memory without bound. Absence from status means "not observed", not zero.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from langgraph.runtime import get_runtime

ToolDiscoveryOperation = Literal["search", "describe", "promote"]

MAX_SESSIONS = 256
RETENTION_SECONDS = 3600.0
MAX_COUNTER_VALUE = (1 << 63) - 1

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDiscoveryCounters:
    search: int
    describe: int
    promote: int


@dataclass
class _RetainedCounters:
    values: ToolDiscoveryCounters
    updated_at: float


def _session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


class ToolDiscoveryMetricsStore:
    """Thread-safe LRU/TTL store containing counters only."""

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        retention_seconds: float = RETENTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self._max_sessions = max_sessions
        self._retention_seconds = retention_seconds
        self._clock = clock
        self._sessions: OrderedDict[str, _RetainedCounters] = OrderedDict()
        self._lock = threading.RLock()

    def _prune(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._sessions.items() if now - entry.updated_at >= self._retention_seconds]
        for key in expired:
            self._sessions.pop(key, None)

    def record(self, session_id: str, operation: ToolDiscoveryOperation) -> bool:
        if not session_id:
            return False
        if operation not in ("search", "describe", "promote"):
            raise ValueError(f"unsupported tool discovery operation: {operation!r}")
        key = _session_key(session_id)
        now = self._clock()
        with self._lock:
            self._prune()
            entry = self._sessions.get(key)
            if entry is None:
                values = ToolDiscoveryCounters(search=0, describe=0, promote=0)
                entry = _RetainedCounters(values=values, updated_at=now)
                self._sessions[key] = entry
            current = getattr(entry.values, operation)
            entry.values = replace(entry.values, **{operation: min(MAX_COUNTER_VALUE, current + 1)})
            entry.updated_at = now
            self._sessions.move_to_end(key)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
        return True

    def counters(self, session_id: str) -> ToolDiscoveryCounters | None:
        if not session_id:
            return None
        key = _session_key(session_id)
        with self._lock:
            self._prune()
            entry = self._sessions.get(key)
            if entry is None:
                return None
            self._sessions.move_to_end(key)
            return entry.values

    def snapshot(self) -> tuple[tuple[str, ToolDiscoveryCounters], ...]:
        with self._lock:
            self._prune()
            return tuple((key, entry.values) for key, entry in self._sessions.items())

    def status(self, session_id: str | None = None) -> dict[str, object]:
        with self._lock:
            self._prune()
            retained = len(self._sessions)
        base: dict[str, object] = {
            "retention_seconds": self._retention_seconds,
            "max_sessions": self._max_sessions,
            "persistence": "process_memory_only",
            "raw_queries_retained": False,
            "session_identity": "sha256_prefix_16",
        }
        if session_id:
            counters = self.counters(session_id)
            if counters is None:
                return {"available": False, "reason": "no_counters_recorded", **base}
            return {
                "available": True,
                "reason": None,
                "session_key": _session_key(session_id),
                "search": counters.search,
                "describe": counters.describe,
                "promote": counters.promote,
                **base,
            }
        return {
            "available": retained > 0,
            "reason": None if retained else "no_counters_recorded",
            "retained_sessions": retained,
            **base,
        }


_METRICS = ToolDiscoveryMetricsStore()


def _runtime_session_id(runtime) -> str | None:
    context = getattr(runtime, "context", None)
    if not isinstance(context, Mapping):
        return None
    session_id = context.get("thread_id")
    return str(session_id) if isinstance(session_id, str) and session_id else None


def _record_for_runtime(runtime, operation: ToolDiscoveryOperation) -> bool:
    session_id = _runtime_session_id(runtime)
    if session_id is None:
        return False
    try:
        return _METRICS.record(session_id, operation)
    except Exception:
        # Telemetry is observational and must never change tool behavior.
        logger.warning("Failed to record tool-discovery telemetry operation=%s", operation, exc_info=True)
        return False


def record_tool_discovery_operation(operation: ToolDiscoveryOperation) -> bool:
    """Record one operation for the active LangGraph thread, if available."""
    try:
        runtime = get_runtime()
    except Exception:
        return False
    return _record_for_runtime(runtime, operation)


def _effective_tool_search_promotions(runtime, names: Sequence[str]) -> list[str]:
    """Apply the current policy decision for telemetry accuracy only.

    Authorization remains enforced by ``SkillToolPolicyMiddleware``. This
    projection merely prevents an outer policy wrapper from turning our count
    into a false effective-promotion claim. Malformed/absent policy data is
    handled conservatively for telemetry: an explicit allowlist is applied;
    otherwise the tool-search proposal is reported.
    """
    context = getattr(runtime, "context", None)
    if not isinstance(context, Mapping):
        return list(names)
    try:
        from alpha.runtime.secret_context import SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY

        decision = context.get(SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY)
    except Exception:
        return list(names)
    if not isinstance(decision, Mapping) or "allowed_names" not in decision:
        return list(names)
    allowed = decision.get("allowed_names")
    if allowed is None:
        return list(names)
    if not isinstance(allowed, list) or not all(isinstance(name, str) for name in allowed):
        return []
    allowed_names = set(allowed)
    return [name for name in names if name in allowed_names]


def record_deferred_tool_search(runtime, names: Sequence[str]) -> bool:
    """Record search plus successful describe/promote stages for one call."""
    if not _record_for_runtime(runtime, "search"):
        return False
    if names:
        _record_for_runtime(runtime, "describe")
        if _effective_tool_search_promotions(runtime, names):
            _record_for_runtime(runtime, "promote")
    return True


def record_current_deferred_tool_search(names: Sequence[str]) -> bool:
    """Record deferred stages for the current LangGraph tool invocation."""
    try:
        runtime = get_runtime()
    except Exception:
        return False
    return record_deferred_tool_search(runtime, names)


def get_tool_discovery_status(session_id: str | None = None) -> dict[str, object]:
    """Return honest bounded status; absent observations are not rendered as zero."""
    return _METRICS.status(session_id)


def get_tool_discovery_snapshot() -> tuple[tuple[str, ToolDiscoveryCounters], ...]:
    """Return the redacted in-memory snapshot for diagnostics and tests."""
    return _METRICS.snapshot()


__all__ = [
    "MAX_COUNTER_VALUE",
    "MAX_SESSIONS",
    "RETENTION_SECONDS",
    "ToolDiscoveryCounters",
    "ToolDiscoveryMetricsStore",
    "ToolDiscoveryOperation",
    "get_tool_discovery_snapshot",
    "get_tool_discovery_status",
    "record_current_deferred_tool_search",
    "record_deferred_tool_search",
    "record_tool_discovery_operation",
]
