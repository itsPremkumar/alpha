"""Memory-tier registry — importability probes over the memory subsystems.

Source of truth: per-subsystem ``importlib.util.find_spec`` probes of the real
``alpha.memory.*`` modules listed in :data:`MEMORY_SUBSYSTEMS`.

Honesty contract:

* The probe measures IMPORTABILITY ONLY. This registry never constructs a
  memory engine (no store is opened, no directory created), so ``health``
  stays ``unverified`` on every descriptor and ``evidence_kind="measured"``
  refers to the module probe, not to any runtime store.
* Probes are isolated per subsystem: one broken package yields one honest
  ``unavailable`` descriptor carrying the real exception text; the other
  descriptors are unaffected, and ``list()`` cannot fail wholesale (``health``
  therefore always reports ``status="ok"`` with the measured descriptor count
  — inspect per-descriptor ``reason`` values for probe failures).
* ``version`` is ``None``: no memory module declares one.
"""

from __future__ import annotations

import importlib.util

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
)

#: (descriptor id, module path) — every entry is a real ``alpha.memory`` module.
MEMORY_SUBSYSTEMS: tuple[tuple[str, str], ...] = (
    ("active_memory", "alpha.memory.active_memory"),
    ("cognitive", "alpha.memory.cognitive"),
    ("session_search", "alpha.memory.session_search"),
    ("kibitzer", "alpha.memory.kibitzer"),
    ("dreaming", "alpha.memory.dreaming"),
    ("wiki_vault", "alpha.memory.wiki_vault"),
)

_AUTHORITY = "runtime (memory subsystems)"
_UNAVAILABLE_REASON = "memory module not importable: {detail}"


class MemoryRegistry:
    """list/describe/health over the memory subsystems (import probes only)."""

    name = "memory"

    def list(self) -> list[CapabilityDescriptor]:
        return [self._probe(entry_id, module) for entry_id, module in MEMORY_SUBSYSTEMS]

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        for known_id, module in MEMORY_SUBSYSTEMS:
            if known_id == entry_id:
                return self._probe(known_id, module)
        return None

    def health(self) -> RegistryHealth:
        # Probes are isolated per entry, so the source (the module table plus
        # the probe call) always answers; count is measured from this call.
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(self.list()),
            error=None,
            evidence_kind="measured",
        )

    @staticmethod
    def _probe(entry_id: str, module: str) -> CapabilityDescriptor:
        try:
            found = importlib.util.find_spec(module)
        except Exception as exc:
            return CapabilityDescriptor(
                id=entry_id,
                kind="memory",
                availability="unavailable",
                source=module,
                version=None,
                health="unverified",
                authority=_AUTHORITY,
                evidence_kind="measured",
                reason=_UNAVAILABLE_REASON.format(detail=f"{type(exc).__name__}: {exc}"),
            )
        if found is None:
            return CapabilityDescriptor(
                id=entry_id,
                kind="memory",
                availability="unavailable",
                source=module,
                version=None,
                health="unverified",
                authority=_AUTHORITY,
                evidence_kind="measured",
                reason=_UNAVAILABLE_REASON.format(detail=f"module not found: {module}"),
            )
        return CapabilityDescriptor(
            id=entry_id,
            kind="memory",
            availability="available",
            source=module,
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
        )
