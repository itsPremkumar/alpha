"""Lazy, fail-open loader for Alpha's optional capabilities.

Capabilities are imported **only** when an operator opts in, so the default
runtime pulls in no extra dependencies. Any failure is captured into the status
report instead of propagating: a broken optional subsystem must never take the
Gateway down (same invariant as the autonomy loops).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from alpha.capabilities.catalog import CAPABILITY_CATALOG, CapabilitySpec
from alpha.config.capabilities_config import CapabilitiesConfig

logger = logging.getLogger(__name__)


def resolve(capability_id: str, config: CapabilitiesConfig) -> CapabilitySpec | None:
    """Return the spec for ``capability_id`` if it is enabled, else ``None``."""
    spec = CAPABILITY_CATALOG.get(capability_id)
    if spec is None:
        return None
    if not config.is_enabled(capability_id, spec.default_enabled):
        return None
    return spec


def load(capability_id: str, config: CapabilitiesConfig) -> Any:
    """Import and instantiate one enabled capability.

    Returns the class/function object named by the spec — the caller decides
    whether to instantiate it. Raises ``KeyError`` for unknown ids and
    ``ImportError``/``AttributeError`` for a broken target; use
    :func:`load_enabled_capabilities` when you want the fail-open variant.
    """
    spec = CAPABILITY_CATALOG[capability_id]
    module = importlib.import_module(spec.module)
    return getattr(module, spec.target)


def load_enabled_capabilities(config: CapabilitiesConfig | None = None) -> dict[str, Any]:
    """Load every enabled capability. Never raises; failures are logged."""
    config = config or CapabilitiesConfig()
    loaded: dict[str, Any] = {}
    for capability_id in CAPABILITY_CATALOG:
        if resolve(capability_id, config) is None:
            continue
        try:
            loaded[capability_id] = load(capability_id, config)
        except Exception as exc:  # pragma: no cover - defensive, fail-open
            logger.warning("capability %s failed to load: %s", capability_id, exc)
    return loaded


def status(config: CapabilitiesConfig | None = None) -> dict[str, dict[str, Any]]:
    """Per-capability status for ``/api/ops/integration-health`` and the UI.

    Each entry reports whether the capability is enabled, whether its target
    imports cleanly, and the module/target it resolves to.
    """
    config = config or CapabilitiesConfig()
    report: dict[str, dict[str, Any]] = {}
    for capability_id, spec in CAPABILITY_CATALOG.items():
        enabled = config.is_enabled(capability_id, spec.default_enabled)
        entry: dict[str, Any] = {
            "enabled": enabled,
            "module": spec.module,
            "target": spec.target,
            "kind": spec.kind,
            "description": spec.description,
            "loadable": False,
        }
        if enabled:
            try:
                load(capability_id, config)
                entry["loadable"] = True
            except Exception as exc:
                entry["loadable"] = False
                entry["error"] = f"{type(exc).__name__}: {exc}"
        report[capability_id] = entry
    return report
