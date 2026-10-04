"""Wiring registry — read-only view of the manifest's router/middleware/loop rows.

Source of truth: ``contracts/feature_manifest.json`` → ``routers``,
``middlewares``, ``loops``, produced by ``backend/scripts/generate_feature_manifest.py``.
Each row already carries the ``wiring_point`` that proves the entry is referenced
at an assembly site, plus ``wired`` / ``state``.

This is the third shape of the same idea the plane already has, and it is worth
being explicit about which is which:

* ``alpha.capabilities.registry.status`` probes *enabled optional subsystems* at
  load time.
* ``GET /api/ops/integration-health`` reports *coverage* over the whole manifest.
* This registry answers a different question — "is this one named router actually
  mounted?" — in the same ``list`` / ``describe`` / ``health`` shape as every other
  selection-plane registry, so a planner can hold it in the same hand.

Honesty contract
----------------
* ``availability="available"`` = the row is present **and** ``wired`` is true,
  i.e. the generator found it referenced at its declared wiring point. That is a
  measured static fact.
* ``availability="unavailable"`` = the row exists but is unwired, and ``reason``
  carries the real reason text (which wiring point was searched, what the row
  claimed). An unwired middleware is a defect to report, not an entry to hide.
* ``health`` stays ``unverified``: a static import-graph fact is not runtime
  health, and no middleware is executed here.
* ``version`` stays ``None`` — the manifest declares none.
* Descriptor ids are namespaced per class (``router:<id>``, ``middleware:<id>``,
  ``loop:<id>``) because the three sections can legitimately contain the same
  token, and a flat id space would let one class's row shadow another's.
* A missing or malformed artifact raises :class:`RegistryUnavailable` with the
  real exception text rather than reading as "nothing is wired".
"""

from __future__ import annotations

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
)
from alpha.workflow.registry.manifest_source import SOURCE, manifest_section

_AUTHORITY = "developer code (generated contracts/feature_manifest.json)"

#: manifest section -> (descriptor kind, id prefix). The tuple's third element is
#: the manifest section name, kept separate from the id prefix because they are
#: not the same word: section ``routers`` uses id prefix ``router``. Deriving one
#: from the other by pluralising is how ``#routeres`` happens.
_WIRING_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("routers", "router", "router"),
    ("middlewares", "middleware", "middleware"),
    ("loops", "supervisor_loop", "loop"),
)


def _wiring_point(row: dict) -> str:
    point = row.get("wiring_point")
    return str(point) if isinstance(point, str) and point else "(no wiring_point declared)"


def _entry_id(row: dict, prefix: str) -> str:
    """Namespaced descriptor id for one manifest row."""
    raw = row.get("id")
    token = str(raw) if isinstance(raw, str) and raw else _wiring_point(row)
    return f"{prefix}:{token}"


class WiringRegistry:
    """list/describe/health over the manifest's wiring rows for all three classes."""

    name = "wiring"

    def list(self) -> list[CapabilityDescriptor]:
        descriptors: list[CapabilityDescriptor] = []
        for section, kind, prefix in _WIRING_SECTIONS:
            descriptors.extend(self._describe_section(section, kind, prefix))
        return descriptors

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        for section, kind, prefix in _WIRING_SECTIONS:
            for row in manifest_section(section):
                if _entry_id(row, prefix) == entry_id:
                    return self._describe_row(row, section, kind, prefix)
        return None

    def health(self) -> RegistryHealth:
        try:
            descriptors = self.list()
        except Exception as exc:
            return RegistryHealth(
                registry=self.name,
                status="unavailable",
                count=None,
                error=f"{type(exc).__name__}: {exc}",
                evidence_kind="measured",
            )
        # The source was read, so the status stays "ok" even when rows are
        # unwired — a defect in the tree is a finding, not an unreadable source.
        # Reporting it through `error` keeps it visible without inventing an
        # "unavailable" registry, which would falsely suggest the manifest is gone.
        unwired = [descriptor.id for descriptor in descriptors if descriptor.availability == "unavailable"]
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=f"{len(unwired)} row(s) declared but unwired: {', '.join(unwired[:5])}" if unwired else None,
            evidence_kind="measured",
        )

    def _describe_section(self, section: str, kind: str, prefix: str) -> list[CapabilityDescriptor]:
        return [self._describe_row(row, section, kind, prefix) for row in manifest_section(section)]

    def _describe_row(self, row: dict, section: str, kind: str, prefix: str) -> CapabilityDescriptor:
        wired = row.get("wired") is True
        state = str(row.get("state") or ("wired" if wired else "unwired"))
        return CapabilityDescriptor(
            id=_entry_id(row, prefix),
            kind=kind,
            availability="available" if wired else "unavailable",
            source=f"{SOURCE}#{section} -> {_wiring_point(row)}",
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=None if wired else f"manifest state={state!r}: no reference found at the declared wiring point",
        )


__all__ = ["WiringRegistry"]
