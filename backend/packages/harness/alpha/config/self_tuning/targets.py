"""Code-owned allowlist of configuration targets eligible for self-tuning.

The registry follows the *change authorization* boundary described by Google
SRE in *Release Engineering*: a production change is safe to automate only
when its candidate space and blast radius are explicit.  The numerical domain
for every target is read from the real owning Pydantic model at import time;
policy floors and ceilings are narrower, explicit guardrails rather than a
hand-copied substitute for that schema.

Protected paths are checked before allowlisting.  Their refusal is a distinct,
disclosed authority boundary, not a validation warning that a caller may
ignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from annotated_types import Ge, Gt, Le, Lt
from pydantic import BaseModel, TypeAdapter, ValidationError

from alpha.config.autonomy_config import AutonomyBusConfig, MetacognitionConfig
from alpha.config.memory_config import L1MemoryConfig, MemoryConfig
from alpha.config.subagent_batches_config import SubagentBatchesConfig
from alpha.config.system_one_config import SystemOneConfig

from .config import PROTECTED_PATH_REASONS, SelfTuningConfig
from .models import BlastRadius


@dataclass(frozen=True, slots=True)
class ValueDomain:
    """Allowed value domain derived from the owning Pydantic ``FieldInfo``."""

    annotation: Any
    schema_floor: float | None
    schema_ceiling: float | None
    floor_inclusive: bool
    ceiling_inclusive: bool

    def coerce(self, value: Any) -> Any:
        """Validate/coerce through the real Pydantic annotation."""
        return TypeAdapter(self.annotation).validate_python(value)

    def describe(self) -> str:
        """Return a concise human-readable domain description."""
        lower = "-inf" if self.schema_floor is None else str(self.schema_floor)
        upper = "inf" if self.schema_ceiling is None else str(self.schema_ceiling)
        left_bracket = "[" if self.floor_inclusive or self.schema_floor is None else "("
        right_bracket = "]" if self.ceiling_inclusive or self.schema_ceiling is None else ")"
        return f"{self.annotation!r} in {left_bracket}{lower}, {upper}{right_bracket}"


@dataclass(frozen=True, slots=True)
class ConfigTarget:
    """One declared, numerically bounded, non-protected config target."""

    path: str
    owner_path: tuple[str, ...]
    owner_model: type[BaseModel]
    field_name: str
    domain: ValueDomain
    floor: float
    ceiling: float
    hot_reloadable: bool
    blast_radius: BlastRadius


class PathResolutionStatus(StrEnum):
    """Closed result of a target lookup."""

    ALLOWED = "allowed"
    PROTECTED = "protected"
    UNDECLARED = "undeclared"


@dataclass(frozen=True, slots=True)
class PathResolution:
    """Target lookup result; protected and undeclared are intentionally distinct."""

    status: PathResolutionStatus
    reason: str
    target: ConfigTarget | None = None


@dataclass(frozen=True, slots=True)
class _TargetSpec:
    path: str
    owner_path: tuple[str, ...]
    owner_model: type[BaseModel]
    field_name: str
    floor: float
    ceiling: float
    hot_reloadable: bool
    blast_radius: BlastRadius


def _numeric_domain(owner_model: type[BaseModel], field_name: str) -> ValueDomain:
    """Introspect a numeric Pydantic field, including its real metadata bounds."""
    field = owner_model.model_fields[field_name]
    annotation = field.annotation
    if annotation not in (int, float):
        raise TypeError(f"self-tuning target {owner_model.__name__}.{field_name} must be int/float, got {annotation!r}")

    schema_floor: float | None = None
    schema_ceiling: float | None = None
    floor_inclusive = True
    ceiling_inclusive = True
    for constraint in field.metadata:
        if isinstance(constraint, Ge):
            schema_floor = float(constraint.ge)
            floor_inclusive = True
        elif isinstance(constraint, Gt):
            schema_floor = float(constraint.gt)
            floor_inclusive = False
        elif isinstance(constraint, Le):
            schema_ceiling = float(constraint.le)
            ceiling_inclusive = True
        elif isinstance(constraint, Lt):
            schema_ceiling = float(constraint.lt)
            ceiling_inclusive = False
    return ValueDomain(
        annotation=annotation,
        schema_floor=schema_floor,
        schema_ceiling=schema_ceiling,
        floor_inclusive=floor_inclusive,
        ceiling_inclusive=ceiling_inclusive,
    )


def _build_target(spec: _TargetSpec) -> ConfigTarget:
    """Build and sanity-check a target against the owning Pydantic model."""
    if spec.floor > spec.ceiling:
        raise ValueError(f"{spec.path} has policy floor {spec.floor} above ceiling {spec.ceiling}")
    domain = _numeric_domain(spec.owner_model, spec.field_name)
    if domain.schema_floor is not None:
        valid_floor = spec.floor >= domain.schema_floor if domain.floor_inclusive else spec.floor > domain.schema_floor
        if not valid_floor:
            raise ValueError(f"{spec.path} policy floor violates its owning model domain")
    if domain.schema_ceiling is not None:
        valid_ceiling = spec.ceiling <= domain.schema_ceiling if domain.ceiling_inclusive else spec.ceiling < domain.schema_ceiling
        if not valid_ceiling:
            raise ValueError(f"{spec.path} policy ceiling violates its owning model domain")
    return ConfigTarget(
        path=spec.path,
        owner_path=spec.owner_path,
        owner_model=spec.owner_model,
        field_name=spec.field_name,
        domain=domain,
        floor=spec.floor,
        ceiling=spec.ceiling,
        hot_reloadable=spec.hot_reloadable,
        blast_radius=spec.blast_radius,
    )


# Deliberately small.  Each entry names its owning model and nested owner path;
# value types and schema bounds are introspected, not copied from YAML.
_TARGET_SPECS: Final[tuple[_TargetSpec, ...]] = (
    _TargetSpec("autonomy.bus.queue_maxsize", ("autonomy", "bus"), AutonomyBusConfig, "queue_maxsize", 32, 4096, False, BlastRadius.PROCESS),
    _TargetSpec("autonomy.bus.handler_timeout_seconds", ("autonomy", "bus"), AutonomyBusConfig, "handler_timeout_seconds", 1.0, 300.0, False, BlastRadius.PROCESS),
    _TargetSpec("autonomy.metacognition.confidence", ("autonomy", "metacognition"), MetacognitionConfig, "confidence", 0.5, 0.99, False, BlastRadius.INSTANCE),
    _TargetSpec("autonomy.metacognition.window", ("autonomy", "metacognition"), MetacognitionConfig, "window", 3, 50, False, BlastRadius.INSTANCE),
    _TargetSpec("memory.l1.debounce_seconds", ("memory", "l1"), L1MemoryConfig, "debounce_seconds", 1.0, 120.0, True, BlastRadius.INSTANCE),
    _TargetSpec("memory.l1.max_memories_per_run", ("memory", "l1"), L1MemoryConfig, "max_memories_per_run", 4, 100, True, BlastRadius.INSTANCE),
    _TargetSpec("memory.l1.recall_top_k", ("memory", "l1"), L1MemoryConfig, "recall_top_k", 1, 25, True, BlastRadius.INSTANCE),
    _TargetSpec("system_one.timeout_ms", ("system_one",), SystemOneConfig, "timeout_ms", 500, 10_000, True, BlastRadius.INSTANCE),
    _TargetSpec("system_one.max_retries", ("system_one",), SystemOneConfig, "max_retries", 0, 3, True, BlastRadius.INSTANCE),
    _TargetSpec("subagent_batches.max_live_items_per_batch", ("subagent_batches",), SubagentBatchesConfig, "max_live_items_per_batch", 10, 50_000, False, BlastRadius.DEPLOYMENT),
)

# Build once as immutable instance data.  This is code-owned policy, not runtime
# mutable registry state.
DEFAULT_TARGETS: Final[tuple[ConfigTarget, ...]] = tuple(_build_target(spec) for spec in _TARGET_SPECS)


class TargetRegistry:
    """Closed target registry with a hard protected-path pre-check."""

    def __init__(self, config: SelfTuningConfig) -> None:
        self._config = config
        self._targets = {target.path: target for target in DEFAULT_TARGETS}
        collisions = [target.path for target in DEFAULT_TARGETS if self._matching_prefix(target.path, config.protected_paths) is not None]
        if collisions:
            raise RuntimeError(f"protected target declarations would widen authority: {collisions}")

    @property
    def bounds_source(self) -> str:
        """Expose the code-owned bounds source selected by config."""
        return self._config.bounds_source

    def targets(self) -> tuple[ConfigTarget, ...]:
        """Return the immutable declared target set."""
        return DEFAULT_TARGETS

    @staticmethod
    def _matching_prefix(path: str, prefixes: tuple[str, ...]) -> str | None:
        matches = [prefix for prefix in prefixes if path == prefix or path.startswith(f"{prefix}.")]
        return max(matches, key=len) if matches else None

    def protected_reason(self, path: str) -> str | None:
        """Return the most-specific mandatory/operator-added protection reason."""
        mandatory = self._matching_prefix(path, tuple(PROTECTED_PATH_REASONS))
        if mandatory is not None:
            return PROTECTED_PATH_REASONS[mandatory]
        configured = self._matching_prefix(path, self._config.protected_paths)
        if configured is not None:
            return f"operator-added protected path prefix {configured!r}"
        return None

    def is_protected(self, path: str) -> bool:
        """Return whether *path* is categorically outside proposal authority."""
        return self.protected_reason(path) is not None

    def resolve(self, path: str) -> PathResolution:
        """Resolve a path without ever falling through a protected prefix."""
        protected_reason = self.protected_reason(path)
        if protected_reason is not None:
            return PathResolution(PathResolutionStatus.PROTECTED, protected_reason)
        target = self._targets.get(path)
        if target is None:
            return PathResolution(PathResolutionStatus.UNDECLARED, f"{path!r} is not a declared self-tuning target")
        return PathResolution(PathResolutionStatus.ALLOWED, f"{path!r} is a declared self-tuning target", target)


def default_off_subsystem_paths() -> frozenset[str]:
    """Introspect promoted memory sections whose real model default is OFF.

    This intentionally reads ``MemoryConfig`` rather than maintaining a second
    list.  A new default-off memory subsystem is therefore covered as soon as
    the central config owner promotes it.
    """
    memory = MemoryConfig()
    paths: set[str] = set()
    for field_name in MemoryConfig.model_fields:
        section = getattr(memory, field_name, None)
        if section is not None and getattr(section, "enabled", None) is False:
            paths.add(f"memory.{field_name}.enabled")
    return frozenset(paths)


def coerce_target_value(target: ConfigTarget, value: Any) -> Any:
    """Coerce through the real domain and convert Pydantic errors to ``ValueError``."""
    if isinstance(value, bool):
        raise ValueError(f"{target.path} rejected boolean {value!r} for a numeric target")
    try:
        return target.domain.coerce(value)
    except ValidationError as exc:
        raise ValueError(f"{target.path} rejected value {value!r}: {exc.errors()[0]['msg']}") from exc
