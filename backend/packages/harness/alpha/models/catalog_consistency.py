"""Cross-namespace model-capability consistency check.

Alpha resolves models from four independent declarations:

1. ``config.yaml -> models[]`` — the authoritative, runtime-built catalog.
2. ``alpha.models.provider_manager.PROVIDER_SPECS`` — the bring-your-own-provider
   catalog, merged into ``GET /api/models`` by ``list_models``.
3. ``alpha.models.free_router`` — the keyless gateway catalog, also merged in.
4. The frontend's fallback provider catalog, previously a hand-copy of (2).

They are merged by name with first-wins de-duplication, so a name declared in
more than one place keeps ``models[]``'s capabilities at the API boundary while
the *other* declaration keeps claiming something different. That drift is
invisible at runtime and shows up as a UI control the backend will reject — the
shipped default did exactly this: ``union-alpha`` was ``supports_thinking:
false`` in ``config.example.yaml`` (pinned by ``tests/test_model_config.py``) and
``true`` in ``PROVIDER_SPECS`` and in the frontend copy.

This module is the single place that compares the namespaces so the drift
becomes a startup error (or an explicit, visible warning in strict mode)
instead of a control that lies. Hand-maintained capability tables are the
industry's most common source of exactly this bug; nothing stops it recurring
except checking it on every boot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Capabilities compared across namespaces. ``context_window`` is included
#: because a wrong window silently corrupts the "% context used" indicator and
#: the thresholds that fraction-based summarization triggers resolve from.
#: ``reasoning_efforts`` is included because a drifted ladder is the effort
#: picker's version of the same bug: the UI offers exactly the rungs the
#: namespace advertised, and a rung the factory then clamps is a control that
#: silently changes the level the user picked.
COMPARED_FIELDS: tuple[str, ...] = ("supports_thinking", "supports_vision", "supports_reasoning_effort", "reasoning_efforts", "context_window")


@dataclass
class CatalogDrift:
    """One model name whose capabilities disagree between two namespaces."""

    name: str
    field_name: str
    configured: Any
    other: Any
    other_source: str

    def describe(self) -> str:
        return f"model '{self.name}': {self.field_name} is {self.configured!r} in config `models[]` but {self.other!r} in {self.other_source}"


@dataclass
class CatalogReport:
    """Outcome of a consistency sweep."""

    drifts: list[CatalogDrift] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.drifts

    def describe(self) -> str:
        if self.ok:
            return f"{self.checked} model name(s) checked across namespaces; no capability drift."
        lines = [f"{len(self.drifts)} capability drift(s) across {self.checked} checked model name(s):"]
        lines.extend(f"  - {d.describe()}" for d in self.drifts)
        return "\n".join(lines)


def _normalized(value: Any) -> Any:
    """Compare booleans and ints by value; treat ``None`` as 'undeclared'.

    ``None`` on either side is not drift: it means the namespace simply did not
    declare the capability, which is legitimate (the free router, for example,
    may know a model is free without knowing its context window).

    A ``reasoning_efforts`` list is normalized onto the canonical ladder first,
    so a namespace that lists ``[max, low]`` and one that lists ``[low, max]``
    agree. Comparing raw lists would report an ordering difference as drift,
    which would train operators to ignore this check.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        from alpha.config.reasoning_effort import canonical_order

        ordered = canonical_order(list(value))
        # An empty declaration is "undeclared", not "declares zero rungs", so it
        # must stay comparable to an absent value on the other side.
        return tuple(ordered) or None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    return value


def check_model_catalog_consistency(app_config: Any, *, include_free_router: bool = True, include_provider_catalog: bool = True) -> CatalogReport:
    """Compare ``models[]`` against the other model namespaces.

    Only names present in BOTH namespaces can drift, so the check is cheap and
    bounded by the configured catalog size. Returns a :class:`CatalogReport`;
    the caller decides whether a non-empty ``drifts`` list is fatal.
    """
    report = CatalogReport()
    configured: dict[str, Any] = {}
    for model in getattr(app_config, "models", []) or []:
        configured[model.name] = model

    other_namespaces: list[tuple[str, dict[str, dict[str, Any]]]] = []

    if include_provider_catalog:
        try:
            from alpha.models.provider_manager import PROVIDER_SPECS

            entries: dict[str, dict[str, Any]] = {}
            for spec in PROVIDER_SPECS:
                for descriptor in spec.default_models:
                    entries.setdefault(
                        descriptor.id,
                        {
                            "supports_thinking": bool(getattr(descriptor, "supports_thinking", False)),
                            "supports_vision": None,
                            "supports_reasoning_effort": False,
                            "reasoning_efforts": getattr(descriptor, "reasoning_efforts", None),
                            "context_window": None,
                            "_source": f"provider_manager.PROVIDER_SPECS[{spec.id}]",
                        },
                    )
            other_namespaces.append(("provider_manager.PROVIDER_SPECS", entries))
        except Exception:
            logger.debug("Provider catalog unavailable for consistency check", exc_info=True)

    if include_free_router:
        try:
            from alpha.models.free_router import get_free_router

            entries = {}
            for free_model in get_free_router().available_free_models():
                entries.setdefault(
                    free_model["id"],
                    {
                        # Read each capability from what the entry actually
                        # declares. Hardcoding `False` here made every
                        # `models[]` entry that legitimately supports thinking
                        # or an effort look like drift against this namespace,
                        # which is the exact failure this module exists to
                        # catch - so it must never invent a value either way.
                        "supports_thinking": free_model.get("supports_thinking"),
                        "supports_vision": None,
                        "supports_reasoning_effort": free_model.get("supports_reasoning_effort"),
                        "reasoning_efforts": free_model.get("reasoning_efforts"),
                        "context_window": free_model.get("context_window"),
                        "_source": "free_router catalog",
                    },
                )
            other_namespaces.append(("free_router catalog", entries))
        except Exception:
            logger.debug("Free router catalog unavailable for consistency check", exc_info=True)

    for _namespace_label, entries in other_namespaces:
        for name, entry in entries.items():
            model = configured.get(name)
            if model is None:
                # Declared only in the auxiliary namespace — not drift.
                continue
            report.checked += 1
            for field_name in COMPARED_FIELDS:
                configured_value = _normalized(getattr(model, field_name, None))
                other_value = _normalized(entry.get(field_name))
                if configured_value is None or other_value is None:
                    continue
                if configured_value != other_value:
                    report.drifts.append(
                        CatalogDrift(
                            name=name,
                            field_name=field_name,
                            configured=configured_value,
                            other=other_value,
                            other_source=entry.get("_source", "auxiliary catalog"),
                        )
                    )
    return report


def enforce_model_catalog_consistency(app_config: Any, *, strict: bool = True) -> CatalogReport:
    """Run the consistency check and surface drift.

    ``strict=True`` logs every drift at ERROR and returns the report so a
    startup hook or test can fail on it. ``strict=False`` downgrades to
    WARNING, for deployments that legitimately prefer the auxiliary catalog's
    view for a name also present in ``models[]``.
    """
    report = check_model_catalog_consistency(app_config)
    if report.ok:
        logger.info("Model catalog consistency: %s", report.describe())
        return report
    level = logging.ERROR if strict else logging.WARNING
    logger.log(
        level,
        "Model catalog capability drift detected.\n%s\n"
        "The API merges these namespaces first-wins by name, so `models[]` wins at runtime while the "
        "other declaration still claims a different capability. A UI built from the drifted value offers "
        "a control the backend rejects. Fix the non-`models[]` declaration to match `models[]`.",
        report.describe(),
    )
    return report
