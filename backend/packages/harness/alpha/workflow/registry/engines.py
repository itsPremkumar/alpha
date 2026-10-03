"""Engine-package registry — read-only view of the generated manifest's engine rows.

Source of truth: ``contracts/feature_manifest.json`` → ``engines``, produced by
``backend/scripts/generate_feature_manifest.py::collect_engines``. That collector
is deliberately mechanical — *a direct child directory of ``alpha/`` that declares
``__init__.py``, contains a submodule, or holds a ``*.py``* — so the number is
reproducible and the definition is arguable in review rather than in prose.

What this registry adds over the raw rows is the projection the selection plane
needs: a stable id, the availability claim with a reason when it is absent, and
an honest statement of what kind of evidence supports it.

Honesty contract
----------------
* ``availability="available"`` = the engine module resolved under an
  importable prefix, i.e. ``alpha.<pkg>`` or ``alpha.<pkg>.<sub>`` has a
  discoverable spec. That is a **measured importability probe**, not a claim that
  anything loaded or works.
* The manifest's own ``kind`` (``package`` / ``namespace``) is carried as the
  descriptor ``kind`` so a namespace row is never presented as a package.
* ``version`` stays ``None``. No engine declares one and this registry never
  invents version strings.
* ``health`` stays ``unverified``. Importability is not runtime health.
* An unreadable artifact raises :class:`RegistryUnavailable` from ``list()`` with
  the real exception text — a missing ``contracts/`` directory must read as
  ``unavailable``, never as "this build has no engines".
* Probes are isolated **per engine**: one unimportable package yields one honest
  ``unavailable`` descriptor carrying the real exception, and the rest of the
  registry is unaffected.
"""

from __future__ import annotations

import importlib.util

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
)
from alpha.workflow.registry.manifest_source import SOURCE, manifest_section

_SECTION = "engines"
_AUTHORITY = "developer code (generated contracts/feature_manifest.json)"
_UNAVAILABLE_REASON = "engine module not importable: {detail}"


def _engine_submodules(row: dict) -> tuple[str, ...]:
    """Submodule names a manifest engine row declares.

    Returns an empty tuple for a malformed row rather than raising: the row is
    still a real engine entry, and dropping it because one field is odd would
    hide a generator change rather than surface it.
    """
    raw = row.get("submodules")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, (str, int)) and str(item))


def _probe_prefixes(row: dict) -> tuple[str, ...]:
    """Import prefixes this engine row could resolve under, most specific first.

    A namespace row has no ``__init__.py`` of its own, so ``find_spec("alpha.x")``
    returns ``None`` for a perfectly healthy namespace. Falling back to its first
    submodule is what makes the probe measure the row rather than Python's
    packaging rules.
    """
    engine_id = str(row.get("id") or "")
    if not engine_id:
        return ()
    prefixes = [engine_id]
    for submodule in _engine_submodules(row):
        # ``submodules`` holds names relative to the engine id, but a generator
        # change could make them dotted already; only extend when they are not.
        if submodule.startswith(engine_id):
            prefixes.append(submodule)
        else:
            prefixes.append(f"{engine_id}.{submodule}")
    return tuple(dict.fromkeys(prefixes))


class EngineRegistry:
    """list/describe/health over the generated engine package rows."""

    name = "engines"

    def list(self) -> list[CapabilityDescriptor]:
        return [self._describe_row(row) for row in manifest_section(_SECTION)]

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        for row in manifest_section(_SECTION):
            if str(row.get("id") or "") == entry_id:
                return self._describe_row(row)
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
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=None,
            evidence_kind="measured",
        )

    def _describe_row(self, row: dict) -> CapabilityDescriptor:
        engine_id = str(row.get("id") or "")
        row_kind = str(row.get("kind") or "engine")
        submodules = _engine_submodules(row)
        detail_suffix = f" ({len(submodules)} submodule(s))" if submodules else ""

        if not engine_id:
            return CapabilityDescriptor(
                id="",
                kind=row_kind,
                availability="unavailable",
                source=SOURCE,
                version=None,
                health="unverified",
                authority=_AUTHORITY,
                evidence_kind="measured",
                reason="manifest engine row has no id; the generator output is malformed",
            )

        last_error = ""
        for prefix in _probe_prefixes(row) or ():
            try:
                found = importlib.util.find_spec(prefix)
            except Exception as exc:  # noqa: BLE001 - probe failure is evidence
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if found is not None:
                return CapabilityDescriptor(
                    id=engine_id,
                    kind=row_kind,
                    availability="available",
                    source=f"{SOURCE}#engines -> {prefix}{detail_suffix}",
                    version=None,
                    health="unverified",
                    authority=_AUTHORITY,
                    evidence_kind="measured",
                )
            last_error = f"module not found: {prefix}"

        return CapabilityDescriptor(
            id=engine_id,
            kind=row_kind,
            availability="unavailable",
            source=f"{SOURCE}#engines{detail_suffix}",
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=_UNAVAILABLE_REASON.format(detail=last_error or f"no import prefix derived for {engine_id!r}"),
        )


__all__ = ["EngineRegistry"]
