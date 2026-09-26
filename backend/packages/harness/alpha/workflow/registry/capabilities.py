"""Capability-catalog registry — read-only view of ``alpha.capabilities.catalog``.

Source of truth: the shipped :data:`alpha.capabilities.catalog.CAPABILITY_CATALOG`
(the operator-facing optional-subsystem catalogue). Per-entry availability is a
REAL ``importlib.util.find_spec`` probe of the entry's module, isolated per
entry: one broken module yields one honest ``unavailable`` descriptor with the
real exception text, never a collapsed list.

Probed = importability only. This registry never imports the target modules and
never claims a capability is loaded or healthy — that check exists in
``alpha.capabilities.registry.status`` (only for *enabled* entries). Hence
``health="unverified"`` on every descriptor here.
"""

from __future__ import annotations

import importlib.util

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_SOURCE = "alpha.capabilities.catalog:CAPABILITY_CATALOG"
_UNAVAILABLE_REASON = "capability module not importable: {detail}"


class CapabilityCatalogRegistry:
    """list/describe/health over the capability catalogue."""

    name = "capabilities"

    def list(self) -> list[CapabilityDescriptor]:
        try:
            from alpha.capabilities.catalog import CAPABILITY_CATALOG
        except Exception as exc:  # pragma: no cover - catalog import is production-wired
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return [self._describe_entry(entry_id, spec) for entry_id, spec in CAPABILITY_CATALOG.items()]

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        try:
            from alpha.capabilities.catalog import CAPABILITY_CATALOG
        except Exception as exc:  # pragma: no cover - catalog import is production-wired
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        spec = CAPABILITY_CATALOG.get(entry_id)
        if spec is None:
            return None
        return self._describe_entry(entry_id, spec)

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
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=None,
            evidence_kind="measured",
        )

    def _describe_entry(self, entry_id: str, spec: object) -> CapabilityDescriptor:
        module = getattr(spec, "module", "")
        spec_kind = getattr(spec, "kind", None) or "capability"
        try:
            found = importlib.util.find_spec(module)
        except Exception as exc:
            # Probe failure is itself measured evidence: report it honestly.
            return CapabilityDescriptor(
                id=entry_id,
                kind=str(spec_kind),
                availability="unavailable",
                source=module or _SOURCE,
                version=None,
                health="unverified",
                authority="operator config (CapabilitiesConfig) + developer code",
                evidence_kind="measured",
                reason=_UNAVAILABLE_REASON.format(detail=f"{type(exc).__name__}: {exc}"),
            )
        if found is None:
            return CapabilityDescriptor(
                id=entry_id,
                kind=str(spec_kind),
                availability="unavailable",
                source=module or _SOURCE,
                version=None,
                health="unverified",
                authority="operator config (CapabilitiesConfig) + developer code",
                evidence_kind="measured",
                reason=_UNAVAILABLE_REASON.format(detail=f"module not found: {module}"),
            )
        return CapabilityDescriptor(
            id=entry_id,
            kind=str(spec_kind),
            availability="available",
            source=module,
            version=None,
            health="unverified",
            authority="operator config (CapabilitiesConfig) + developer code",
            evidence_kind="measured",
        )
