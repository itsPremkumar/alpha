"""Thread-safe registry for pluggable memory recall surfaces."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from typing import Any

from .models import SCENARIOS, MemorySurface, Scenario, parse_scenario


class DuplicateSurfaceError(ValueError):
    """Raised when a second surface would shadow an existing name."""


class SurfaceNotFoundError(KeyError):
    """Raised by strict lookups for an unregistered surface."""


class RegistryDescription(dict[str, dict[str, Any]]):
    """A JSON-friendly registry description with convenient attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - normal dict protocol
            raise AttributeError(name) from exc


class MemorySurfaceRegistry:
    """Register surface metadata without importing the surface owner.

    The registry is intentionally a metadata boundary.  A subsystem owner can
    register a name before its implementation package exists, and the router
    will later consume caller-supplied blocks under that name.
    """

    def __init__(self, surfaces: Iterable[MemorySurface | Mapping[str, Any]] | None = None) -> None:
        self._lock = threading.RLock()
        self._surfaces: dict[str, MemorySurface] = {}
        for surface in surfaces or ():
            self.register(surface)

    @staticmethod
    def _key(name: str) -> str:
        return str(name).strip().lower()

    def register(
        self,
        surface: MemorySurface | Mapping[str, Any] | str | None = None,
        **fields: Any,
    ) -> MemorySurface:
        """Register one surface and return its validated model.

        ``register(MemorySurface(...))`` is the normal form.  The mapping and
        keyword forms make the ownership seam easy for another package to call
        without importing this module's concrete model more than once.
        """

        if isinstance(surface, MemorySurface):
            if fields:
                raise TypeError("keyword fields cannot be combined with a MemorySurface instance")
            validated = surface
            key = self._key(validated.name)
        else:
            if isinstance(surface, str):
                data: dict[str, Any] = {"name": surface, **fields}
            elif isinstance(surface, Mapping):
                data = {**dict(surface), **fields}
            elif surface is None:
                data = dict(fields)
            else:
                data = {"name": surface, **fields}  # let pydantic produce the useful error
            candidate = data.get("name")
            if candidate is None:
                raise ValueError("memory surface registration requires a name")
            key = self._key(str(candidate))
            if not key:
                raise ValueError("memory surface registration requires a non-empty name")
            validated = MemorySurface.model_validate(data)
        with self._lock:
            if key in self._surfaces:
                raise DuplicateSurfaceError(f"memory surface already registered: {validated.name!r}")
            self._surfaces[key] = validated
        return validated.model_copy(deep=True)

    register_surface = register

    def unregister(self, name: str) -> bool:
        """Remove a surface, returning whether it was present."""

        key = self._key(name)
        with self._lock:
            return self._surfaces.pop(key, None) is not None

    remove = unregister

    def get(self, name: str, *, required: bool = False) -> MemorySurface | None:
        """Return a defensive copy, optionally raising when absent."""

        key = self._key(name)
        with self._lock:
            surface = self._surfaces.get(key)
            result = surface.model_copy(deep=True) if surface is not None else None
        if result is None and required:
            raise SurfaceNotFoundError(name)
        return result

    def list(self, scenario: Scenario | str | None = None) -> list[MemorySurface]:
        """List defensive copies, optionally filtered by served scenario."""

        parsed = parse_scenario(scenario, default=None) if scenario is not None else None
        if scenario is not None and parsed is None:
            return []
        with self._lock:
            surfaces = list(self._surfaces.values())
        if parsed is not None:
            surfaces = [surface for surface in surfaces if parsed in surface.scenarios]
        return [surface.model_copy(deep=True) for surface in surfaces]

    list_surfaces = list

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(surface.name for surface in self._surfaces.values())

    def describe(self, name: str | None = None) -> RegistryDescription | dict[str, Any] | None:
        """Describe one surface or all surfaces as JSON-compatible data."""

        if name is not None:
            surface = self.get(name)
            return RegistryDescription(surface.to_dict()) if surface is not None else None
        return RegistryDescription((surface.name, RegistryDescription(surface.to_dict())) for surface in self.list())

    def snapshot(self) -> RegistryDescription:
        return self.describe()  # type: ignore[return-value]

    def clear(self) -> None:
        with self._lock:
            self._surfaces.clear()

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return self._key(str(name)) in self._surfaces

    def __len__(self) -> int:
        with self._lock:
            return len(self._surfaces)


def _default_surfaces() -> tuple[MemorySurface, ...]:
    """Metadata for surfaces that exist in Alpha today.

    These values are intentionally conservative.  They describe the currently
    injected surfaces without pretending that the wave packages are wired.
    """

    every = tuple(SCENARIOS)
    return (
        MemorySurface(
            name="l1",
            description="Typed L1 scene-segmented working memory and user directives.",
            scenarios=every,
            base_weight=0.90,
            cost_units=3,
            priority=90,
        ),
        MemorySurface(
            name="cognitive",
            description="Cognitive working memory, procedural playbooks, and belief surfaces.",
            scenarios=every,
            base_weight=0.78,
            cost_units=2,
            priority=80,
        ),
        MemorySurface(
            name="dormant_context",
            description="Low-priority dormant context retained for selective recall.",
            scenarios=every,
            base_weight=0.35,
            cost_units=1,
            priority=35,
        ),
    )


DEFAULT_SURFACES: tuple[MemorySurface, ...] = _default_surfaces()
# A named default registry is part of the public seam.  Consumers that need
# isolation should construct MemorySurfaceRegistry() or build_default_registry().
DEFAULT_REGISTRY = MemorySurfaceRegistry(DEFAULT_SURFACES)
DEFAULT_MEMORY_SURFACES = DEFAULT_SURFACES


def build_default_registry() -> MemorySurfaceRegistry:
    """Return a fresh default registry for hermetic callers."""

    return MemorySurfaceRegistry(DEFAULT_SURFACES)


def get_default_registry() -> MemorySurfaceRegistry:
    """Return the process default registry explicitly requested by a caller."""

    return DEFAULT_REGISTRY


def get_surface_registry() -> MemorySurfaceRegistry:
    return get_default_registry()


def default_registry() -> MemorySurfaceRegistry:
    return get_default_registry()


def reset_default_registry() -> None:
    """Restore the built-in metadata after an explicit test/owner mutation."""

    DEFAULT_REGISTRY.clear()
    for surface in DEFAULT_SURFACES:
        DEFAULT_REGISTRY.register(surface)


def register_surface(
    surface: MemorySurface | Mapping[str, Any] | str,
    **fields: Any,
) -> MemorySurface:
    """Register a surface on the explicit default registry."""

    return DEFAULT_REGISTRY.register(surface, **fields)


def unregister_surface(name: str) -> bool:
    return DEFAULT_REGISTRY.unregister(name)


__all__ = [
    "DEFAULT_MEMORY_SURFACES",
    "DEFAULT_REGISTRY",
    "DEFAULT_SURFACES",
    "DuplicateSurfaceError",
    "MemorySurfaceRegistry",
    "RegistryDescription",
    "SurfaceNotFoundError",
    "build_default_registry",
    "default_registry",
    "get_default_registry",
    "get_surface_registry",
    "register_surface",
    "reset_default_registry",
    "unregister_surface",
]
