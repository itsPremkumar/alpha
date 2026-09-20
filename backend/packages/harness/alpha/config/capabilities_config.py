"""Opt-in capability configuration.

Alpha ships a number of complete but *optional* subsystems (swarm auctions, MoA
deliberation, the SDLC engine, ...). Historically these existed in the tree with
no production reference at all, so nothing could load them and the orphan-module
guard could only flag them. :mod:`alpha.capabilities` is the fix: every optional
subsystem is declared in one catalogue and becomes loadable through this config.

Design invariants (same contract as the autonomy subsystem):
* A capability is OFF unless it is explicitly enabled here. Enabling nothing
  changes no runtime behaviour.
* Loading is fail-open and lazy: an import error or a missing optional
  dependency is reported in ``status()``, never raised into request handling.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CapabilitiesConfig(BaseModel):
    """Master switch plus per-capability opt-in flags.

    Keys are capability ids from :data:`alpha.capabilities.catalog.CAPABILITY_CATALOG`.
    An id absent from ``enabled`` falls back to the catalogue's
    ``default_enabled`` (``False`` for every entry today).
    """

    enabled: bool = Field(
        default=True,
        description="Master switch for the capability loader. False = no optional subsystem is ever imported.",
    )
    capabilities: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-capability opt-in flags keyed by capability id; absent ids use the catalogue default (off).",
    )

    def is_enabled(self, capability_id: str, default: bool = False) -> bool:
        """Resolve one capability id to an on/off decision."""
        if not self.enabled:
            return False
        return bool(self.capabilities.get(capability_id, default))
