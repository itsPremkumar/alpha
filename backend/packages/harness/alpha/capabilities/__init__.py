"""Optional (opt-in) capabilities: catalogue + lazy loader.

See :mod:`alpha.capabilities.catalog` for the declared subsystems and
:mod:`alpha.capabilities.registry` for the loader and status reporting.
"""

from __future__ import annotations

from alpha.capabilities.catalog import CAPABILITY_CATALOG, CapabilitySpec, capability_ids
from alpha.capabilities.registry import load, load_enabled_capabilities, resolve, status

__all__ = [
    "CAPABILITY_CATALOG",
    "CapabilitySpec",
    "capability_ids",
    "load",
    "load_enabled_capabilities",
    "resolve",
    "status",
]
