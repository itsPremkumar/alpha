"""Thread-safe bounded registry for named health probes."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator

from .models import ComponentHealth
from .probes import Clock, Probe, _checked_at


class RegistryError(ValueError):
    """Base class for explicit registry configuration errors."""


class DuplicateProbeError(RegistryError):
    """Raised when a component name is registered more than once."""


class RegistryCapacityError(RegistryError):
    """Raised when a registration would exceed the configured component cap."""


class HealthProbeRegistry:
    """Store probes by stable component name with deterministic snapshots.

    The registry owns no implicit/global state.  A caller must pass the
    registry instance it wants to use, and every snapshot is sorted by name.
    Probe results are copied at the boundary so a caller can freely mutate a
    returned evidence dictionary without changing the stored result.
    """

    def __init__(self, max_components: int = 64, *, clock: Clock | None = None) -> None:
        if not isinstance(max_components, int) or isinstance(max_components, bool) or max_components < 1:
            raise ValueError("max_components must be a positive integer")
        self._max_components = max_components
        self._clock = clock
        self._probes: dict[str, Probe] = {}
        self._last_results: dict[str, ComponentHealth] = {}
        self._lock = threading.RLock()

    @property
    def max_components(self) -> int:
        """The hard registration cap."""

        return self._max_components

    @property
    def clock(self) -> Clock | None:
        """The explicitly injected result clock, if one was supplied."""

        return self._clock

    def register(self, name: str | Probe, probe: Probe | None = None) -> Probe:
        """Register a probe and return it.

        Both ``register("l1", probe)`` and ``register(probe)`` are accepted.
        A duplicate is rejected rather than replacing a prior collaborator;
        replacing a probe must be an explicit unregister/register operation.
        """

        if not isinstance(name, str):
            if probe is not None:
                raise TypeError("pass either a probe or a name and probe, not both")
            selected = name
            selected_name = str(getattr(selected, "name", "")).strip()
            if not selected_name:
                raise RegistryError("probe must expose a non-empty name")
        else:
            selected_name = str(name).strip()
            selected = probe
            if not selected_name:
                raise RegistryError("probe name must not be empty")
            if selected is None:
                raise TypeError("probe is required")
        with self._lock:
            if selected_name in self._probes:
                raise DuplicateProbeError(f"health probe {selected_name!r} is already registered")
            if len(self._probes) >= self._max_components:
                raise RegistryCapacityError(f"health probe registry capacity {self._max_components} reached; refusing to register {selected_name!r}")
            self._probes[selected_name] = selected
            return selected

    def unregister(self, name: str | Probe) -> Probe | None:
        """Remove a named probe, returning the removed object if present."""

        selected_name = self._name_for(name)
        with self._lock:
            removed = self._probes.pop(selected_name, None)
            self._last_results.pop(selected_name, None)
            return removed

    def reset(self) -> None:
        """Remove all probes and cached results (primarily a test seam)."""

        with self._lock:
            self._probes.clear()
            self._last_results.clear()

    def get(self, name: str) -> Probe | None:
        """Return the registered probe object, or ``None`` when absent.

        Probe collaborators are intentionally not copied: copying an injected
        service could break its identity or lifecycle.  Health *records*
        returned by :meth:`check` and :meth:`check_all` are deep copies.
        """

        selected_name = str(name).strip()
        with self._lock:
            return self._probes.get(selected_name)

    def names(self) -> tuple[str, ...]:
        """Return names in deterministic lexical order."""

        with self._lock:
            return tuple(sorted(self._probes))

    def items(self) -> tuple[tuple[str, Probe], ...]:
        """Return a deterministic snapshot of registered pairs."""

        with self._lock:
            return tuple((name, self._probes[name]) for name in sorted(self._probes))

    def probes(self) -> tuple[Probe, ...]:
        """Return registered probes in deterministic name order."""

        return tuple(probe for _, probe in self.items())

    def check(self, name: str) -> ComponentHealth:
        """Run one probe and return a defensive copy of its record."""

        selected_name = str(name).strip()
        with self._lock:
            probe = self._probes.get(selected_name)
        if probe is None:
            record = self._stamp(
                ComponentHealth(
                    name=selected_name or "unknown",
                    state="unavailable",
                    reason=f"health probe {selected_name!r} is not registered",
                    evidence={"registered": False},
                )
            )
        else:
            record = self._invoke(selected_name, probe)
        copied = record.model_copy(deep=True)
        with self._lock:
            self._last_results[selected_name] = copied.model_copy(deep=True)
        return copied

    def check_all(self) -> tuple[ComponentHealth, ...]:
        """Run every probe once in lexical order and return copied records."""

        with self._lock:
            pairs = tuple((name, self._probes[name]) for name in sorted(self._probes))
        records = tuple(self._invoke(name, probe) for name, probe in pairs)
        copied = tuple(record.model_copy(deep=True) for record in records)
        with self._lock:
            self._last_results = {record.name: record.model_copy(deep=True) for record in copied}
        return copied

    def last_results(self) -> tuple[ComponentHealth, ...]:
        """Return defensive copies of the last check results."""

        with self._lock:
            return tuple(self._last_results[name].model_copy(deep=True) for name in sorted(self._last_results))

    def _name_for(self, name: str | Probe) -> str:
        if not isinstance(name, str):
            selected = str(getattr(name, "name", "")).strip()
        else:
            selected = str(name).strip()
        if not selected:
            raise RegistryError("probe name must not be empty")
        return selected

    def _invoke(self, name: str, probe: Probe) -> ComponentHealth:
        checker: Callable[[], ComponentHealth] | None = getattr(probe, "check", None)
        if checker is None or not callable(checker):
            checker = getattr(probe, "probe", None)
        if checker is None or not callable(checker):
            checker = getattr(probe, "run", None)
        if checker is None or not callable(checker):
            return self._stamp(
                ComponentHealth(
                    name=name,
                    state="unavailable",
                    reason="registered object does not implement check(), probe(), or run()",
                    evidence={"probe_type": type(probe).__name__},
                )
            )
        try:
            value = checker()
        except Exception as exc:  # noqa: BLE001 - registry must disclose, not propagate
            return self._stamp(
                ComponentHealth(
                    name=name,
                    state="unavailable",
                    reason=f"probe raised {type(exc).__name__}: {str(exc).strip()}",
                    evidence={"exception_type": type(exc).__name__},
                )
            )
        if not isinstance(value, ComponentHealth):
            return self._stamp(
                ComponentHealth(
                    name=name,
                    state="unavailable",
                    reason=f"probe returned {type(value).__name__}; expected ComponentHealth",
                    evidence={"returned_type": type(value).__name__},
                )
            )
        return self._stamp(value.model_copy(deep=True, update={"name": name}))

    def _stamp(self, record: ComponentHealth) -> ComponentHealth:
        if record.checked_at is not None or self._clock is None:
            return record
        checked_at = _checked_at(self._clock)
        return record if checked_at is None else record.model_copy(update={"checked_at": checked_at})

    def __contains__(self, name: object) -> bool:
        selected = str(name).strip()
        with self._lock:
            return selected in self._probes

    def __len__(self) -> int:
        with self._lock:
            return len(self._probes)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())


__all__ = [
    "DuplicateProbeError",
    "HealthProbeRegistry",
    "RegistryCapacityError",
    "RegistryError",
]
