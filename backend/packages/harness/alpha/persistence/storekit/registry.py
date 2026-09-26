"""Declarative inventory of stores, formats, conventions, and footprints.

The registry deliberately knows nothing about store implementations.  A
deployment supplies a :class:`StoreRegistration` (or uses one of the
conservative defaults below) and can then answer two operational questions
without importing every memory package:

* which registered documents are behind the target format version, and
* how many bytes/files the declared conventions currently occupy.

The root convention is a relative glob, never an imported path resolver.  A
host with a different layout registers its own convention.  This keeps the
registry cycle-free and makes a footprint report honest: it reports what was
actually found under the declared patterns, not an estimate derived from a
store's in-memory cache.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_STORE_REGISTRATIONS",
    "MigrationRequirement",
    "RegistryFootprint",
    "RegistryReport",
    "StoreRegistration",
    "StoreRegistry",
    "build_default_registry",
    "register_store",
]


@dataclass(frozen=True, slots=True)
class StoreRegistration:
    """One declarative store description."""

    name: str
    format_version: int
    root_convention: str
    document_glob: str = ""
    migration_chain: tuple[str, ...] = ()
    target_version: int | None = None
    enabled: bool = False
    kind: str = "json"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("store registration name must be non-empty")
        if int(self.format_version) < 1:
            raise ValueError("format_version must be positive")
        if self.target_version is not None and int(self.target_version) < 1:
            raise ValueError("target_version must be positive or None")
        convention = str(self.root_convention).strip()
        if not convention:
            raise ValueError("root_convention must be non-empty")
        if Path(convention).is_absolute() or ".." in Path(convention).parts:
            raise ValueError("root_convention must be relative and must not contain '..'")
        pattern = str(self.document_glob).strip()
        if pattern and (Path(pattern).is_absolute() or ".." in Path(pattern).parts):
            raise ValueError("document_glob must be relative and must not contain '..'")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "format_version", int(self.format_version))
        object.__setattr__(self, "target_version", None if self.target_version is None else int(self.target_version))
        object.__setattr__(self, "root_convention", convention)
        object.__setattr__(self, "document_glob", pattern)
        object.__setattr__(self, "migration_chain", tuple(str(item) for item in self.migration_chain))
        object.__setattr__(self, "kind", str(self.kind or "json"))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def effective_target(self) -> int:
        return self.format_version if self.target_version is None else self.target_version

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "format_version": self.format_version,
            "target_version": self.target_version,
            "root_convention": self.root_convention,
            "document_glob": self.document_glob,
            "migration_chain": list(self.migration_chain),
            "enabled": self.enabled,
            "kind": self.kind,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class MigrationRequirement:
    """One answer to ``what must be migrated?``."""

    store: str
    current_version: int
    target_version: int
    reason: str
    migration_chain: tuple[str, ...]

    @property
    def required(self) -> bool:
        return self.reason == "migration_required"

    def to_dict(self) -> dict[str, Any]:
        return {
            "store": self.store,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "reason": self.reason,
            "migration_chain": list(self.migration_chain),
        }


@dataclass(frozen=True, slots=True)
class RegistryFootprint:
    """Files and bytes discovered under declared conventions."""

    root: Path | None
    files: int = 0
    bytes: int = 0
    by_store: Mapping[str, int] = field(default_factory=dict)
    missing_patterns: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root) if self.root is not None else None,
            "files": self.files,
            "bytes": self.bytes,
            "by_store": dict(self.by_store),
            "missing_patterns": list(self.missing_patterns),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class RegistryReport:
    """Combined migration and footprint disclosure."""

    entries: tuple[StoreRegistration, ...]
    migrations: tuple[MigrationRequirement, ...]
    footprint: RegistryFootprint

    @property
    def migration_required(self) -> tuple[MigrationRequirement, ...]:
        return tuple(item for item in self.migrations if item.required)

    @property
    def total_bytes(self) -> int:
        return self.footprint.bytes

    @property
    def total_files(self) -> int:
        return self.footprint.files

    def to_dict(self) -> dict[str, Any]:
        return {
            "stores": [entry.to_dict() for entry in self.entries],
            "migrations": [item.to_dict() for item in self.migrations],
            "migration_required": [item.store for item in self.migration_required],
            "footprint": self.footprint.to_dict(),
        }


#: Conservative declarations matching the layouts documented by the current
#: stores.  They are data, not imports; a subsystem with a different layout can
#: replace or extend its entry without changing this package.
DEFAULT_STORE_REGISTRATIONS: tuple[StoreRegistration, ...] = (
    StoreRegistration("l1.records", 1, "users/{user_id}/l1", "users/*/l1/records/*.json", ("v1",), kind="json"),
    StoreRegistration("affective.events", 1, "users/{user_id}/affective", "users/*/affective/events.json", ("v1",), kind="json"),
    StoreRegistration("prospective.items", 1, "users/{user_id}/prospective", "users/*/prospective/items.json", ("v1",), kind="json"),
    StoreRegistration("entities.store", 1, "users/{user_id}/entities", "users/*/entities/store.json", ("v1",), kind="json"),
    StoreRegistration("social.state", 1, "users/{owner_scope}/social", "users/*/social/state.json", ("v1",), kind="json"),
    StoreRegistration("narrative", 1, "narrative/{scope}", "narrative/*/*.json", ("v1",), kind="json"),
    StoreRegistration("fabric.envelopes", 2, "memory_fabric/scopes", "memory_fabric/scopes/**/*.json", ("v1", "v2"), kind="json"),
    StoreRegistration("policy.provenance", 1, "users/{user_id}/policy", "users/*/policy/**/*.jsonl", ("v1",), kind="jsonl"),
    StoreRegistration("evaluation.cases", 1, "memory/evaluation/cases", "memory/evaluation/cases/*.json", ("v1",), kind="json"),
    StoreRegistration("scenarios.provenance", 1, "users/{user_id}/scenarios", "users/*/scenarios/**/*.jsonl", ("v1",), kind="jsonl"),
    StoreRegistration("fusion.provenance", 1, "users/{user_id}/fusion", "users/*/fusion/**/*.jsonl", ("v1",), kind="jsonl"),
)


class StoreRegistry:
    """In-memory inventory that performs no sibling-package imports."""

    def __init__(self, entries: Iterable[StoreRegistration | Mapping[str, Any]] | None = None) -> None:
        self._entries: dict[str, StoreRegistration] = {}
        for entry in entries or ():
            self.register(entry)

    @property
    def entries(self) -> tuple[StoreRegistration, ...]:
        return tuple(self._entries[name] for name in sorted(self._entries))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def register(self, entry: StoreRegistration | Mapping[str, Any]) -> StoreRegistration:
        if isinstance(entry, Mapping):
            entry = StoreRegistration(**dict(entry))
        if not isinstance(entry, StoreRegistration):
            raise TypeError("register expects StoreRegistration or mapping")
        if entry.name in self._entries:
            raise ValueError(f"store {entry.name!r} is already registered")
        self._entries[entry.name] = entry
        return entry

    def get(self, name: str) -> StoreRegistration:
        try:
            return self._entries[str(name)]
        except KeyError as exc:
            raise KeyError(f"unknown store registration: {name!r}") from exc

    def migration_status(self, *, target_version: int | None = None) -> tuple[MigrationRequirement, ...]:
        """Report each entry's version relationship to the target."""

        results: list[MigrationRequirement] = []
        for entry in self.entries:
            target = entry.effective_target if target_version is None else int(target_version)
            if entry.format_version < target:
                reason = "migration_required"
            elif entry.format_version > target:
                reason = "unsupported_future_version"
            else:
                reason = "up_to_date"
            results.append(
                MigrationRequirement(
                    store=entry.name,
                    current_version=entry.format_version,
                    target_version=target,
                    reason=reason,
                    migration_chain=entry.migration_chain,
                )
            )
        return tuple(results)

    def what_needs_migration(self, *, target_version: int | None = None) -> tuple[MigrationRequirement, ...]:
        return tuple(item for item in self.migration_status(target_version=target_version) if item.required)

    def footprint(
        self,
        root: Path | str | None,
        *,
        include_corrupt: bool = False,
        include_locks: bool = False,
    ) -> RegistryFootprint:
        """Sum declared files under ``root`` without importing their stores."""

        if root is None:
            return RegistryFootprint(None)
        base = Path(root).expanduser().resolve(strict=False)
        by_store: dict[str, int] = {}
        missing: list[str] = []
        errors: list[str] = []
        total_files = 0
        total_bytes = 0
        for entry in self.entries:
            if not entry.document_glob:
                by_store[entry.name] = 0
                continue
            matches: set[Path] = set()
            try:
                matches.update(path for path in base.glob(entry.document_glob) if path.is_file())
            except (OSError, ValueError) as exc:
                errors.append(f"{entry.name}: {exc}")
            if include_corrupt:
                for matched in tuple(matches):
                    matches.update(sibling for sibling in matched.parent.glob(f"{matched.name}.corrupt-*") if sibling.is_file())
            else:
                matches = {path for path in matches if ".corrupt-" not in path.name}
            if not include_locks:
                matches = {path for path in matches if not path.name.endswith(".lock")}
            store_bytes = 0
            for path in sorted(matches):
                try:
                    store_bytes += path.stat().st_size
                except OSError as exc:
                    errors.append(f"{entry.name}:{path}: {exc}")
            by_store[entry.name] = store_bytes
            total_files += len(matches)
            total_bytes += store_bytes
            if not matches:
                missing.append(f"{entry.name}:{entry.document_glob}")
        return RegistryFootprint(
            root=base,
            files=total_files,
            bytes=total_bytes,
            by_store=by_store,
            missing_patterns=tuple(missing),
            errors=tuple(errors),
        )

    def report(self, root: Path | str | None = None, *, target_version: int | None = None, include_corrupt: bool = False, include_locks: bool = False) -> RegistryReport:
        return RegistryReport(
            entries=self.entries,
            migrations=self.migration_status(target_version=target_version),
            footprint=self.footprint(root, include_corrupt=include_corrupt, include_locks=include_locks),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"stores": [entry.to_dict() for entry in self.entries]}


def build_default_registry(*, enabled: bool = False, target_version: int | None = None) -> StoreRegistry:
    """Build a registry from data-only declarations, never store imports."""

    return StoreRegistry(
        StoreRegistration(
            entry.name,
            entry.format_version,
            entry.root_convention,
            entry.document_glob,
            entry.migration_chain,
            entry.target_version if target_version is None else target_version,
            enabled,
            entry.kind,
            entry.metadata,
        )
        for entry in DEFAULT_STORE_REGISTRATIONS
    )


def register_store(
    registry: StoreRegistry,
    name: str,
    *,
    format_version: int,
    root_convention: str,
    document_glob: str = "",
    migration_chain: Iterable[str] = (),
    target_version: int | None = None,
    enabled: bool = False,
    kind: str = "json",
    metadata: Mapping[str, Any] | None = None,
) -> StoreRegistration:
    """Functional registration helper for deployment bootstrap code."""

    return registry.register(
        StoreRegistration(
            name=name,
            format_version=format_version,
            root_convention=root_convention,
            document_glob=document_glob,
            migration_chain=tuple(migration_chain),
            target_version=target_version,
            enabled=enabled,
            kind=kind,
            metadata=metadata or {},
        )
    )
