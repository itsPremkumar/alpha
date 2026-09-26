"""Shared descriptor protocol for W-N3's read-only discovery registries.

One descriptor shape (DOC-C selection-plane fields: id, kind, availability,
source, version, health, authority) plus honest-evidence and fail-closed
semantics shared by every registry in ``alpha.workflow.registry``:

* ``availability`` reports what the registry's SOURCE OF TRUTH says — a module
  probe result, a production registration list, an ``enabled`` flag in config.
  It never claims that a subsystem is currently running or connected; that
  would be runtime health.
* ``health`` therefore stays ``unverified`` unless a registry actually probed
  the running subsystem. No W-N3 registry executes a subsystem, so every
  descriptor this wave emits carries ``health="unverified"``.
* ``version`` is ``None`` unless the source itself declares a version. W-N3
  never invents version strings.
* ``evidence_kind`` must say how ``availability`` was established; the allowed
  set is the repo-wide honesty enum.
* ``reason`` is mandatory (non-empty) whenever ``availability`` is
  ``unavailable`` — honest failed status, never a silent empty list.

Registries raise :class:`RegistryUnavailable` from ``list()``/``describe()``
when the underlying source cannot be read at all (fail-closed: the caller must
not mistake a broken source for "zero entries"). ``health()`` never raises; it
converts any failure into an honest ``status="unavailable"`` report with the
real exception text.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

EvidenceKind = Literal["measured", "simulated", "heuristic", "unverified"]
Availability = Literal["available", "unavailable"]
DescriptorHealth = Literal["unverified", "unavailable"]
RegistryStatus = Literal["ok", "unavailable"]


class CapabilityDescriptor(BaseModel):
    """One registrable thing behind the dynamic-workflow discovery plane."""

    id: str = Field(description="Stable identifier within its registry.")
    kind: str = Field(
        description=(
            "The entry's grouping label as declared by its source (e.g. engine/guard "
            "for capability specs); registries whose source declares no label use the "
            "registry kind: tool, skill, mcp_server, memory."
        )
    )
    availability: Availability = Field(
        description=(
            "What the source of truth says (module importable / registered / enabled / "
            "configured). Never a claim that the subsystem is running or connected."
        )
    )
    source: str = Field(description="Where this descriptor was read from.")
    version: str | None = Field(
        default=None,
        description="Declared version, or None when the source declares none (never invented).",
    )
    health: DescriptorHealth = Field(
        default="unverified",
        description="Runtime health; unverified unless a registry actually probed the subsystem.",
    )
    authority: str = Field(
        description="Who may change this entry: operator config, developer code, or runtime."
    )
    evidence_kind: EvidenceKind = Field(
        description="How availability was established (measured probe, config read, ...)."
    )
    reason: str | None = Field(
        default=None,
        description="Honest reason when unavailable (disabled, module missing, read error)."
    )


class RegistryHealth(BaseModel):
    """Per-registry health report; ``health()`` never raises, it reports."""

    registry: str = Field(description="Registry name (capabilities, tools, skills, mcp, memory).")
    status: RegistryStatus = Field(
        description="ok = the source was read; unavailable = the source could not be read."
    )
    count: int | None = Field(
        default=None,
        description="Measured descriptor count when status is ok, else None.",
    )
    error: str | None = Field(
        default=None,
        description="Real exception text when status is unavailable (fail-closed disclosure).",
    )
    evidence_kind: EvidenceKind = Field(
        description="Health() always probes its source, so this is measured.",
    )


class RegistryUnavailable(RuntimeError):
    """The registry's source could not be read — fail closed, do not guess."""


@runtime_checkable
class DescriptorRegistry(Protocol):
    """list / describe / health over capability descriptors (DOC-C protocol)."""

    name: str

    def list(self) -> list[CapabilityDescriptor]:
        """All descriptors; raises RegistryUnavailable when the source is unreadable."""
        ...

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        """One descriptor by id, or None when the id is honestly absent."""
        ...

    def health(self) -> RegistryHealth:
        """Probe the source and report; never raises."""
        ...
