"""Sentinel — autonomous monitor / diagnose / fix / verify / commit loop.

The loop is:

    OBSERVE -> DIAGNOSE -> FIX -> VERIFY -> COMMIT

Each stage is bounded and reversible. A stage that cannot complete escalates
rather than guessing.

See ``docs/SENTINEL_AUTONOMOUS_AGENT_PLAN.md`` for the full design.
"""

from agent_workspace.runtime.sentinel.signals import (
    DEFAULT_SEVERITY,
    SEVERITIES,
    SOURCES,
    AttemptRecord,
    Signal,
    SignalTracker,
    compute_fingerprint,
    dedupe,
    normalize_message,
    sort_by_severity,
)

__all__ = [
    "AttemptRecord",
    "DEFAULT_SEVERITY",
    "SEVERITIES",
    "SOURCES",
    "Signal",
    "SignalTracker",
    "compute_fingerprint",
    "dedupe",
    "normalize_message",
    "sort_by_severity",
]
