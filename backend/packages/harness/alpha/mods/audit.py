"""AuditLedgerMod — the wildcard audit trail for the Alpha Mod Kernel.

Claude Code ships a built-in ``telemetry`` mod and documents the single most
requested audit primitive in the entire mods design as ``on("*")``: *one
function that sees every event, in order, including every plugin's own calls on
``$``*. Without it, reconstructing "which prompt, which tool call, which
subagent dispatch, in what order" means parsing transcript files after the fact.

This mod is that function. It subscribes to every event, wraps the whole chain,
and records what each participating mod decided.

Three properties make it usable as an audit trail rather than a log line:

- **It observes and always continues.** The ledger never denies, rewrites or
  answers. An audit plane that can change what it audits is not an audit plane.
- **It redacts.** The ledger holds tool arguments, which hold credentials. Every
  payload is passed through a secret scrubber before it is stored.
- **It cannot break the pipeline.** Every access is defensive; a ledger that
  raises would deny work it was only meant to record.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable
from typing import Any

from alpha.mods.context import CapabilityContext
from alpha.mods.manifest import ModManifest
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)

#: Secret-shaped content must never land in an audit record.
_SECRET_PATTERNS = (
    "api[_-]?key",
    "secret",
    "password",
    "passwd",
    "token",
    "bearer",
    "authorization",
    "credential",
    "private[_-]?key",
    "access[_-]?key",
    "session[_-]?id",
)

#: Provider token shapes, matched *inside* a string and not only at its start: a
#: tool argument is far more often "my key is sk-..." than a bare token, and a
#: redaction that only matches a leading prefix misses the realistic case.
_EMBEDDED_TOKEN_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"gho_[a-zA-Z0-9]{20,}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[a-zA-Z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bbearer\s+[a-zA-Z0-9._\-]{16,}"),
)


def _looks_sensitive(key: str) -> bool:
    lowered = str(key).lower()
    return any(token in lowered for token in _SECRET_PATTERNS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace credential-shaped values with a redaction marker.

    The walk is bounded in depth and size. An unbounded recursive walk over tool
    arguments is how an audit plane becomes the thing that exhausts memory, and
    the payload being audited is exactly the unbounded input.
    """
    if _depth > 6:
        return "[truncated:depth]"
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if _looks_sensitive(k) else redact(v, _depth=_depth + 1)) for k, v in list(value.items())[:256]}
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in list(value)[:256]]
    if isinstance(value, str):
        for pattern in _EMBEDDED_TOKEN_PATTERNS:
            if pattern.search(value):
                return "[REDACTED]"
        if len(value) > 2000:
            return value[:2000] + "…[truncated]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:512]


def _digest(payload: dict[str, Any]) -> str:
    """A stable short digest of the redacted payload, for correlation records."""
    try:
        blob = json.dumps(redact(payload), sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(payload)[:512]
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class AuditLedgerMod:
    """Records every dispatched event and the mod chain that decided it."""

    name = "audit_ledger"
    version = "1.0.0"
    # Registered in the kernel tier so it wraps the *whole* chain. An audit plane
    # registered last would never see a denial: a terminal DENY at SECURITY stops
    # the chain there, and the mod that was supposed to record it never runs.
    # Being outermost is the only way "every event at once" is literally true.
    priority = int(ModPriority.KERNEL)
    required_capabilities: set[str] = set()
    # ``None`` means every event, including the ``command.run`` and
    # ``tool.completed`` events other mods answer.
    subscribed_events = None
    manifest = ModManifest.create(
        name="audit_ledger",
        version="1.0.0",
        description="Wildcard audit trail: records every dispatched event and the mod chain that decided it.",
        hooks=["*"],
        calls=[],
        state_reads=[],
        state_writes=[],
        gating=False,
    )

    def __init__(self, *, max_entries: int = 5000):
        # The floor keeps the ledger bounded even when asked for a tiny bound:
        # a mod may not disable the bound, only raise it.
        self._max_entries = max(10, int(max_entries))
        self._entries: list[dict[str, Any]] = []
        self._mod_counters: dict[str, int] = {}
        self._outcome_counters: dict[str, int] = {}

    # -- query surface -----------------------------------------------------

    def get_entries(
        self,
        *,
        limit: int = 100,
        event_name: str | None = None,
        mod_name: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recent audit records, newest last, filtered on the given fields."""
        rows = self._entries
        if event_name is not None:
            rows = [r for r in rows if r.get("event") == event_name]
        if mod_name is not None:
            rows = [r for r in rows if mod_name in r.get("chain", [])]
        if outcome is not None:
            rows = [r for r in rows if r.get("outcome") == outcome]
        return [dict(r) for r in rows[-max(0, int(limit)) :]]

    def stats(self) -> dict[str, Any]:
        """Aggregate counters over the retained window."""
        return {
            "retained": len(self._entries),
            "capacity": self._max_entries,
            "events": len({r.get("event") for r in self._entries}),
            "by_mod": dict(sorted(self._mod_counters.items())),
            "by_outcome": dict(sorted(self._outcome_counters.items())),
        }

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        self._mod_counters.clear()
        self._outcome_counters.clear()
        return count

    def export_jsonl(self, *, limit: int = 1000) -> str:
        """Render the retained window as JSONL for an external sink."""
        lines = [json.dumps(r, sort_keys=True, default=str) for r in self._entries[-max(0, int(limit)) :]]
        return "\n".join(lines)

    # -- handler -----------------------------------------------------------

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        """Wrap the chain, record what happened, and always continue."""
        started = time.time()
        # Read the chain *after* the downstream handlers have appended to it.
        result = await next_fn(event)

        try:
            self._record(event, result, time.time() - started)
        except Exception as exc:  # pragma: no cover - the ledger must never deny work
            logger.debug("AuditLedgerMod failed to record '%s': %s", event.name, exc)

        return result

    def _append(self, record: dict[str, Any]) -> None:
        """Append one entry, evicting the oldest past the bound.

        The bound lives *with* the append rather than at the call site, so every
        path that records is bounded by construction — including the one a future
        edit adds without noticing.
        """
        self._entries.append(record)
        overflow = len(self._entries) - self._max_entries
        if overflow > 0:
            del self._entries[:overflow]

    def _record(self, event: AlphaEvent, result: EventResult, duration: float) -> None:
        chain_names: list[str] = []
        for entry in getattr(event, "_mod_chain", None) or []:
            if isinstance(entry, dict) and entry.get("mod"):
                chain_names.append(str(entry["mod"]))

        record: dict[str, Any] = {
            "timestamp": time.time(),
            "event": event.name,
            "event_id": event.event_id,
            "source": event.source,
            "outcome": result.outcome.value if hasattr(result.outcome, "value") else str(result.outcome),
            "reason": str(result.reason or "")[:500],
            "duration_ms": round(duration * 1000, 3),
            "correlation": event.correlation.to_dict(),
            "chain": chain_names,
            "payload_digest": _digest(event.payload if isinstance(event.payload, dict) else {}),
            "payload": redact(event.payload if isinstance(event.payload, dict) else {}),
        }
        if result.metadata.get("mod_rewrites"):
            record["rewrites"] = [{"mod": str(r.get("mod")), "reason": str(r.get("reason"))[:300]} for r in result.metadata["mod_rewrites"] if isinstance(r, dict)]

        self._append(record)

        for name in chain_names:
            self._mod_counters[name] = self._mod_counters.get(name, 0) + 1
        key = str(record["outcome"])
        self._outcome_counters[key] = self._outcome_counters.get(key, 0) + 1


def summarize(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fold audit entries into a compact operator-facing summary."""
    rows = list(entries)
    return {
        "count": len(rows),
        "events": sorted({str(r.get("event")) for r in rows}),
        "outcomes": {outcome: sum(1 for r in rows if r.get("outcome") == outcome) for outcome in sorted({str(r.get("outcome")) for r in rows})},
        "mods": sorted({m for r in rows for m in r.get("chain", []) if isinstance(m, str)}),
    }
