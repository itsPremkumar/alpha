"""Model registry — read-only view of every model setting in ``config.yaml``.

Source of truth: the live ``AppConfig`` — ``models[]``, ``providers:``,
``model_routing``, ``default_model``, ``model_catalog:``, ``free_gateways:`` and
``model_pricing:``. This is deliberately the **one** model namespace: there is no
second model file, so a name absent from here is a name Alpha does not know.

Why this registry exists
------------------------
The existing ``tools`` registry answers "which tools exist" and ``mcp`` answers
"which servers exist", but nothing answered "which LLMs exist" — so the model
surface of the selection plane had a hole exactly where an agent needs it most
(``/model cheap``, routing a subagent, checking whether a vision model is
declared). ``SelfKnowledgeService.models()`` reports counts and names; this
registry carries the same facts through the shared descriptor shape so a planner
can hold tools, skills and models in one collection.

Honesty contract
----------------
* ``availability="available"`` = the model is **declared** in ``models[]``. That
  is a measured config fact. It is emphatically **not** a claim that the provider
  answered, that credentials exist, or that the model is reachable — probing a
  provider costs money and reaches the network, so this registry never does it.
* ``health`` stays ``unverified`` and ``evidence_kind`` is ``"measured"`` against
  the config read, not against the provider.
* ``reason`` on a disabled/partial model carries the real declaration gap.
* ``version`` stays ``None`` — no model declares one here.
* ``authority`` names ``config.yaml`` explicitly, because a model name is an
  operator decision, not developer code.
* Providers are listed in the same registry under their own ids so "which
  gateways are configured" needs no second call.

``get_app_config()`` stats its file on every call to detect edits, so every method
here does blocking I/O: Gateway callers must run this through
``asyncio.to_thread``.
"""

from __future__ import annotations

from typing import Any

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_SOURCE = "config.yaml"
_AUTHORITY = "operator config (config.yaml models/providers/model_routing)"

#: Max model / provider names a descriptor reason may quote. Config can be large
#: and a reason string is not the place to reproduce it.
_MAX_REASON_NAMES = 12


def _names(values: Any, *, limit: int = _MAX_REASON_NAMES) -> str:
    """Render an iterable of names for a reason string, bounded."""
    items = [str(value) for value in values if value]
    if not items:
        return "none declared"
    if len(items) <= limit:
        return ", ".join(items)
    return f"{', '.join(items[:limit])} (+{len(items) - limit} more)"


class ModelRegistry:
    """list/describe/health over the configured model and provider namespaces."""

    name = "models"

    def list(self) -> list[CapabilityDescriptor]:
        config, _source = self._load_config()
        descriptors = [self._describe_model(config, model) for model in getattr(config, "models", None) or []]
        descriptors.extend(self._describe_provider(name, provider) for name, provider in sorted((getattr(config, "providers", None) or {}).items()))
        descriptors.append(self._describe_default(config))
        return descriptors

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        config, _source = self._load_config()
        if entry_id == "default_model":
            return self._describe_default(config)
        for model in getattr(config, "models", None) or []:
            name = str(getattr(model, "name", "") or "")
            if name == entry_id:
                return self._describe_model(config, model)
            # Accept a provider-qualified name so a caller holding a routing slug
            # finds the model instead of an honest "absent".
            if name and entry_id.endswith(f"/{name}"):
                return self._describe_model(config, model)
        for provider_name, provider in sorted((getattr(config, "providers", None) or {}).items()):
            if provider_name == entry_id:
                return self._describe_provider(provider_name, provider)
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

    # -- projections --------------------------------------------------------

    @staticmethod
    def _load_config() -> tuple[Any, str]:
        """Read the live config, or fail closed.

        A config that will not load must read as ``unavailable`` — the single most
        damaging thing Alpha could report about itself is a model list that is
        empty because the file is broken.
        """
        try:
            from alpha.config.app_config import get_app_config
        except Exception as exc:  # pragma: no cover - production-wired import
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        try:
            return get_app_config(), _SOURCE
        except Exception as exc:
            raise RegistryUnavailable(f"config.yaml could not be loaded: {type(exc).__name__}: {exc}") from exc

    def _describe_model(self, config: Any, model: Any) -> CapabilityDescriptor:
        name = str(getattr(model, "name", "") or "")
        use = str(getattr(model, "use", "") or "")
        provider = str(getattr(model, "provider", "") or "")
        model_slug = str(getattr(model, "model", "") or "")
        parts = [part for part in (use or provider, model_slug) if part]
        address = " / ".join(parts) if parts else "(no use/provider/model set)"
        slots = _routing_slots_referencing(config, name)
        if slots:
            # An address, not a claim: these are the slots that literally name
            # this model, so a reader can go verify each one in config.yaml.
            address = f"{address} [routing: {_names(slots)}]"

        if not name:
            return CapabilityDescriptor(
                id="",
                kind="model",
                availability="unavailable",
                source=f"{_SOURCE}#models -> {address}",
                version=None,
                health="unverified",
                authority=_AUTHORITY,
                evidence_kind="measured",
                reason="model entry has no name; the operator config is malformed",
            )

        # Declared, not probed — see the module docstring. Nothing here says the
        # provider answered or that a credential exists.
        return CapabilityDescriptor(
            id=name,
            kind="model",
            availability="available",
            source=f"{_SOURCE}#models -> {address}",
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
        )

    def _describe_provider(self, name: str, provider: Any) -> CapabilityDescriptor:
        use = str(getattr(provider, "use", "") or "")
        return CapabilityDescriptor(
            id=name,
            kind="model_provider",
            availability="available",
            source=f"{_SOURCE}#providers -> {use or '(no use set)'}",
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
        )

    def _describe_default(self, config: Any) -> CapabilityDescriptor:
        default_name = str(getattr(config, "default_model_name", "") or "")
        declared = {str(getattr(model, "name", "") or "") for model in getattr(config, "models", None) or []}
        # AppConfig validates this at load, so a default outside models[] means
        # the config was replaced after load (hot edit). Report it honestly
        # rather than asserting a working default.
        available = bool(default_name) and default_name in declared
        return CapabilityDescriptor(
            id="default_model",
            kind="model_selection",
            availability="available" if available else "unavailable",
            source=f"{_SOURCE}#default_model -> {default_name or '(unset)'}",
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=None if available else f"default_model {default_name or '(unset)'} is not present in models[]; configured models: {_names(sorted(declared))}",
        )


def _routing_slots_referencing(config: Any, model_name: str) -> list[str]:
    """Which ``model_routing`` slots resolve to ``model_name``.

    Walks the routing section's own ``categories`` / ``tiers`` / ``specialists``
    mappings generically rather than hard-coding today's key set, so adding a
    routing dimension later reports here without a second code path. Returns an
    empty list when the slot names cannot be resolved — the caller renders that
    as "no routing slot", not as an error.
    """
    routing = getattr(config, "model_routing", None)
    if routing is None or not model_name:
        return []
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str) and node == model_name:
            found.append(path.lstrip("."))

    walk(getattr(routing, "categories", None), "categories")
    walk(getattr(routing, "tiers", None), "tiers")
    walk(getattr(routing, "specialists", None), "specialists")
    return sorted(set(found))


__all__ = ["ModelRegistry"]
