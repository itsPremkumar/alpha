"""Explicit, stepwise document migrations.

A migration is data, not an implicit ``if version < 2`` branch scattered
through a store.  Each :class:`Migration` names an exact edge
``from_version -> to_version`` and supplies a deterministic ``upgrade`` (and
optionally ``downgrade``) function.  A registry validates the graph before a
plan is run: duplicate source versions are ambiguous, gaps are refused, and
cycles are rejected.  ``migrate_document`` then walks one edge at a time and
validates every intermediate document.

Migration functions receive the *payload* mapping, not the envelope, by
default.  That keeps a store's schema transformation independent of checksum
and timestamp bookkeeping; a caller that needs envelope-level work can wrap
the function itself.  The input is deep-copied before every call, so a
migration that mutates its argument cannot alter the source document.  A
failed step therefore leaves the source document byte-identical: this module
never writes to disk.

``dry_run=True`` executes the same deterministic transformations in memory and
returns the would-be document, but performs no persistence.  Deployments
should treat a dry run as a plan/verification pass, not as a way to make a
non-deterministic migration safe.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .documents import DocumentEnvelope, make_document, validate_document

__all__ = [
    "Migration",
    "MigrationError",
    "MigrationPlan",
    "MigrationRegistry",
    "MigrationResult",
    "migrate_document",
    "plan_migrations",
    "try_plan_migrations",
]

MigrationStep = Callable[[Any], Any]
ValidationHook = Callable[..., Any]


class MigrationError(RuntimeError):
    """A migration graph cannot be planned or safely applied."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.reason = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class Migration:
    """One deterministic edge in a document's format history."""

    from_version: int
    to_version: int
    description: str
    upgrade: MigrationStep
    downgrade: MigrationStep | None = None
    name: str = ""
    validate: ValidationHook | None = None

    def __post_init__(self) -> None:
        if isinstance(self.from_version, bool) or isinstance(self.to_version, bool):
            raise ValueError("migration versions must be integers")
        if int(self.from_version) < 1 or int(self.to_version) < 1:
            raise ValueError("migration versions must be positive")
        if int(self.from_version) == int(self.to_version):
            raise ValueError("a migration must change the format version")
        if not callable(self.upgrade):
            raise TypeError("upgrade must be callable")
        if self.downgrade is not None and not callable(self.downgrade):
            raise TypeError("downgrade must be callable or None")
        if not str(self.description).strip():
            raise ValueError("migration description must be non-empty")
        object.__setattr__(self, "from_version", int(self.from_version))
        object.__setattr__(self, "to_version", int(self.to_version))
        object.__setattr__(self, "name", str(self.name or f"v{self.from_version}->v{self.to_version}"))

    @property
    def identifier(self) -> str:
        return self.name

    def step_for(self, *, downgrade: bool = False) -> MigrationStep | None:
        return self.downgrade if downgrade else self.upgrade


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """An ordered, already-validated plan."""

    current_version: int
    target_version: int
    steps: tuple[Migration, ...]
    direction: str

    def __iter__(self) -> Iterator[Migration]:
        return iter(self.steps)

    def __len__(self) -> int:
        return len(self.steps)

    def __getitem__(self, index: int) -> Migration:
        return self.steps[index]

    @property
    def ok(self) -> bool:
        return True

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.identifier for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "target_version": self.target_version,
            "direction": self.direction,
            "steps": [step.identifier for step in self.steps],
        }


class MigrationRegistry:
    """Validated collection of format edges.

    Registration is intentionally strict: two migrations cannot share a
    source version, and a graph cycle is rejected at registration time.  A
    missing edge is only discovered when a plan is requested, which lets an
    application register migrations in any order while still refusing to run
    an incomplete chain.
    """

    def __init__(self, migrations: Iterable[Migration] | None = None) -> None:
        self._by_source: dict[int, Migration] = {}
        for migration in migrations or ():
            self.register(migration)

    @property
    def migrations(self) -> tuple[Migration, ...]:
        return tuple(self._by_source[version] for version in sorted(self._by_source))

    @property
    def latest_version(self) -> int:
        if not self._by_source:
            return 0
        return max(max(migration.from_version, migration.to_version) for migration in self._by_source.values())

    @property
    def versions(self) -> tuple[int, ...]:
        return tuple(sorted({version for migration in self._by_source.values() for version in (migration.from_version, migration.to_version)}))

    def register(self, migration: Migration) -> MigrationRegistry:
        if not isinstance(migration, Migration):
            raise TypeError("register expects a Migration")
        previous = self._by_source.get(migration.from_version)
        if previous is not None:
            raise MigrationError(
                "duplicate_migration",
                f"version {migration.from_version} already has {previous.identifier!r}; cannot add {migration.identifier!r}",
            )
        self._by_source[migration.from_version] = migration
        try:
            self._reject_cycles()
        except MigrationError:
            self._by_source.pop(migration.from_version, None)
            raise
        return self

    def register_all(self, migrations: Iterable[Migration]) -> MigrationRegistry:
        for migration in migrations:
            self.register(migration)
        return self

    def _reject_cycles(self) -> None:
        graph: dict[int, tuple[int, ...]] = {}
        for migration in self._by_source.values():
            graph.setdefault(migration.from_version, ())
            graph[migration.from_version] = (*graph[migration.from_version], migration.to_version)
        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(version: int) -> None:
            if version in visiting:
                raise MigrationError("migration_cycle", f"cycle reaches format version {version}")
            if version in visited:
                return
            visiting.add(version)
            for target in graph.get(version, ()):
                visit(target)
            visiting.remove(version)
            visited.add(version)

        for version in sorted(graph):
            visit(version)

    def validate(self) -> MigrationRegistry:
        self._reject_cycles()
        return self

    def upgrade_edge(self, version: int) -> Migration | None:
        return self._by_source.get(int(version))

    def chain(self, current_version: int, target_version: int) -> MigrationPlan:
        return plan_migrations(current_version, target_version, self)

    def plan(self, current_version: int, target_version: int) -> MigrationPlan:
        return self.chain(current_version, target_version)

    def describe(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": migration.identifier,
                "from_version": migration.from_version,
                "to_version": migration.to_version,
                "description": migration.description,
                "downgrade": migration.downgrade is not None,
            }
            for migration in self.migrations
        )


def _normalize_version(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise MigrationError("invalid_version", f"{name} must be an integer")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise MigrationError("invalid_version", f"{name} must be an integer") from exc
    if version < 1:
        raise MigrationError("invalid_version", f"{name} must be positive")
    return version


def try_plan_migrations(
    current_version: int,
    target_version: int,
    registry: MigrationRegistry,
) -> tuple[MigrationPlan | None, str, str]:
    """Plan without raising; return ``(plan, reason, error)`` on refusal."""

    try:
        return plan_migrations(current_version, target_version, registry), "", ""
    except MigrationError as exc:
        return None, exc.code, str(exc)


def plan_migrations(
    current_version: int | MigrationRegistry,
    target_version: int,
    registry: MigrationRegistry | int | None = None,
) -> MigrationPlan:
    """Return a complete stepwise plan or raise a coded :class:`MigrationError`.

    Upgrades follow ``from_version`` edges.  Downgrades follow the reverse of
    the same edges and therefore require the corresponding migration to have
    supplied a ``downgrade`` callable.  A current version newer than every
    registered edge is ``unsupported_future_version``: it is never silently
    downgraded or rewritten.
    """

    if isinstance(current_version, MigrationRegistry):
        if not isinstance(registry, int):
            raise TypeError("plan_migrations(registry, current, target) requires integer current_version")
        current = _normalize_version(registry, "current_version")
        target = _normalize_version(target_version, "target_version")
        active = current_version
    else:
        current = _normalize_version(current_version, "current_version")
        target = _normalize_version(target_version, "target_version")
        if registry is None:
            active = MigrationRegistry()
        elif isinstance(registry, MigrationRegistry):
            active = registry
        else:
            raise TypeError("plan_migrations requires a MigrationRegistry")
    active.validate()
    if not active.migrations:
        if current == target:
            return MigrationPlan(current, target, (), "none")
        raise MigrationError("migration_gap", "no migration registry was supplied")
    if current > active.latest_version:
        raise MigrationError(
            "unsupported_future_version",
            f"current format version {current} is newer than registered version {active.latest_version}",
        )
    if current == target:
        return MigrationPlan(current, target, (), "none")
    if target > active.latest_version:
        raise MigrationError(
            "unsupported_future_version",
            f"target format version {target} is newer than registered version {active.latest_version}",
        )
    if current < target:
        steps: list[Migration] = []
        version = current
        while version < target:
            migration = active.upgrade_edge(version)
            if migration is None:
                raise MigrationError("migration_gap", f"no migration starts at format version {version}")
            if migration.to_version <= version:
                raise MigrationError("migration_gap", f"migration {migration.identifier} does not advance beyond {version}")
            steps.append(migration)
            version = migration.to_version
        if version != target:
            raise MigrationError("migration_gap", f"chain ends at {version}, requested {target}")
        return MigrationPlan(current, target, tuple(steps), "upgrade")
    steps = []
    version = current
    while version > target:
        candidates = [item for item in active.migrations if item.to_version == version and item.from_version < version]
        if not candidates:
            raise MigrationError("migration_gap", f"no downgrade leaves format version {version}")
        migration = candidates[0]
        if migration.downgrade is None:
            raise MigrationError("downgrade_unavailable", f"migration {migration.identifier} has no downgrade")
        if migration.from_version < target:
            raise MigrationError("migration_gap", f"downgrade chain would overshoot target {target}")
        steps.append(migration)
        version = migration.from_version
    if version != target:
        raise MigrationError("migration_gap", f"downgrade chain ends at {version}, requested {target}")
    return MigrationPlan(current, target, tuple(steps), "downgrade")


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Disclosed outcome of applying a plan to one document."""

    status: str
    document: DocumentEnvelope | None
    original: DocumentEnvelope | None
    target_version: int
    applied: tuple[str, ...] = ()
    reason: str = ""
    error: str = ""
    dry_run: bool = False
    changed: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "up_to_date", "dry_run"}

    @property
    def would_change(self) -> bool:
        return self.status == "dry_run" and bool(self.applied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target_version": self.target_version,
            "applied": list(self.applied),
            "reason": self.reason,
            "error": self.error,
            "dry_run": self.dry_run,
            "changed": self.changed,
        }


def _looks_like_envelope(value: Mapping[str, Any]) -> bool:
    return {"format_version", "schema_id", "checksum", "payload"}.issubset(value)


def _coerce_document(value: DocumentEnvelope | Mapping[str, Any]) -> DocumentEnvelope:
    if isinstance(value, DocumentEnvelope):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("document must be a DocumentEnvelope or mapping")
    if _looks_like_envelope(value):
        # Do not verify a checksum here: migration code must be able to repair
        # or report a malformed-but-readable document, and checksum handling
        # belongs to documents.load_document.
        validation = validate_document(value, verify_checksum=False)
        if validation.document is not None:
            return validation.document
    return make_document(copy.deepcopy(dict(value)), format_version=1, schema_id="storekit.payload", clock=lambda: 0.0)


def _call_with_optional_context(function: Callable[..., Any], payload: Any, migration: Migration) -> Any:
    """Call a user hook with either ``(payload, migration)`` or ``(payload)``."""

    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(payload, migration)
    positional = [parameter for parameter in signature.parameters.values() if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}]
    has_varargs = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if has_varargs or len(positional) >= 2:
        return function(payload, migration)
    return function(payload)


def _apply_step(payload: Any, migration: Migration, *, downgrade: bool) -> Any:
    function = migration.step_for(downgrade=downgrade)
    if function is None:  # pragma: no cover - guarded by planner
        raise MigrationError("downgrade_unavailable", f"migration {migration.identifier} has no downgrade")
    working = copy.deepcopy(payload)
    result = _call_with_optional_context(function, working, migration)
    return working if result is None else result


def _validate_step(payload: Any, migration: Migration) -> None:
    if migration.validate is None:
        return
    result = _call_with_optional_context(migration.validate, payload, migration)
    if result is False:
        raise ValueError(f"validation hook rejected {migration.identifier}")


def _sealed_with_version(document: DocumentEnvelope, payload: Any, version: int, migration: Migration, clock: Any) -> DocumentEnvelope:
    chain = document.migration_chain
    marker = migration.identifier
    if marker in chain:
        chain = tuple(item for item in chain if item != marker) + (marker,)
    else:
        chain = (*chain, marker)
    timestamp_clock = clock if clock is not None else (lambda: document.updated_at or document.created_at)
    return DocumentEnvelope(
        payload=copy.deepcopy(payload),
        format_version=version,
        schema_id=document.schema_id,
        created_at=document.created_at,
        updated_at=document.created_at if document.created_at else 0.0,
        migration_chain=chain,
        document_id=document.document_id,
        metadata=document.metadata,
    ).sealed(clock=timestamp_clock)


def migrate_document(
    document: DocumentEnvelope | Mapping[str, Any],
    registry: MigrationRegistry,
    target_version: int,
    *,
    dry_run: bool = False,
    validate: ValidationHook | None = None,
    clock: Callable[[], float] | None = None,
) -> MigrationResult:
    """Apply a validated plan stepwise, never persisting anything.

    The source document is not mutated.  On any refusal or failed step the
    returned ``document`` is the original object and ``changed`` is ``False``;
    a caller must therefore only persist when ``result.ok`` and not
    ``result.dry_run`` are both true.
    """

    try:
        original = _coerce_document(document)
        plan = plan_migrations(original.format_version, target_version, registry)
    except MigrationError as exc:
        try:
            original = _coerce_document(document)
        except (TypeError, ValueError):
            original = None
        return MigrationResult(
            status="refused",
            document=original,
            original=original,
            target_version=int(target_version) if isinstance(target_version, int) else 0,
            reason=exc.code,
            error=str(exc),
            dry_run=bool(dry_run),
        )
    except (TypeError, ValueError) as exc:
        return MigrationResult(
            status="refused",
            document=None,
            original=None,
            target_version=int(target_version) if isinstance(target_version, int) else 0,
            reason="invalid_document",
            error=str(exc),
            dry_run=bool(dry_run),
        )
    if not plan.steps:
        return MigrationResult(
            status="up_to_date",
            document=original,
            original=original,
            target_version=original.format_version,
            reason="already_at_target",
            dry_run=bool(dry_run),
        )

    candidate = original
    applied: list[str] = []
    try:
        for migration in plan.steps:
            payload = _apply_step(candidate.payload, migration, downgrade=plan.direction == "downgrade")
            _validate_step(payload, migration)
            if validate is not None:
                external = _call_with_optional_context(validate, payload, migration)
                if external is False:
                    raise ValueError(f"external validation rejected {migration.identifier}")
            candidate = _sealed_with_version(
                candidate,
                payload,
                migration.to_version if plan.direction == "upgrade" else migration.from_version,
                migration,
                clock,
            )
            applied.append(migration.identifier)
    except Exception as exc:  # noqa: BLE001 - a failed step is disclosed data
        return MigrationResult(
            status="failed",
            document=original,
            original=original,
            target_version=plan.target_version,
            applied=tuple(applied),
            reason="migration_step_failed",
            error=str(exc),
            dry_run=bool(dry_run),
        )
    return MigrationResult(
        status="dry_run" if dry_run else "succeeded",
        document=candidate,
        original=original,
        target_version=plan.target_version,
        applied=tuple(applied),
        changed=candidate.payload != original.payload or candidate.format_version != original.format_version,
        dry_run=bool(dry_run),
    )
