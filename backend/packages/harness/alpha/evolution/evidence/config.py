"""Configuration for the self-evolution evidence gate. Default OFF.

Every threshold, floor, required gate and evaluation-surface glob the gate uses
lives on this model. Two rules make it reviewable:

* **nothing is hard-coded downstream.** The comparison layer reads
  ``noise_floors``/``metric_directions``/``min_sample_size``/
  ``max_sample_size_gap`` from here, the gate layer reads ``required_gates`` and
  ``max_touched_paths``, the decision layer reads ``primary_metric`` and
  ``human_classification_patterns``, and the integrity layer reads
  ``integrity_path_globs``. A test asserts every field is read by a consuming
  module, so a key cannot rot into a silent default.
* **unknown keys are an error**, never ignored. A typo in an operator's YAML
  must not quietly leave the gate at a default threshold.

``enabled`` defaults to ``False``: with the default config every entry point is
a no-op that touches no source, runs no command, writes no provenance, and
returns ``insufficient_evidence`` with that reason. The model is frozen, so a
running gate cannot have its thresholds swapped underneath it.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from alpha.evolution.evidence.models import DIRECTIONS, HIGHER_IS_BETTER

#: Lower bounds for the integer knobs, checked in one place.
_INT_MINIMUMS: dict[str, int] = {
    "min_sample_size": 1,
    "max_sample_size_gap": 0,
    "max_touched_paths": 1,
    "max_output_chars": 1,
}

__all__ = [
    "DEFAULT_HUMAN_CLASSIFICATION_PATTERNS",
    "DEFAULT_METRIC_DIRECTIONS",
    "DEFAULT_NOISE_FLOORS",
    "DEFAULT_REQUIRED_GATES",
    "GATE_BLAST_RADIUS",
    "GATE_EVALUATOR_INTEGRITY",
    "GATE_REPRODUCIBILITY",
    "GATE_ROLLBACK_PATH",
    "EvolutionEvidenceConfig",
    "config_from_mapping",
    "load_config",
    "load_evolution_evidence_config",
    "read_config",
]

GATE_EVALUATOR_INTEGRITY = "evaluator_integrity"
GATE_REPRODUCIBILITY = "reproducibility"
GATE_ROLLBACK_PATH = "rollback_path"
GATE_BLAST_RADIUS = "blast_radius"

#: The gates the decision function refuses to run without.
DEFAULT_REQUIRED_GATES: tuple[str, ...] = (GATE_EVALUATOR_INTEGRITY, GATE_REPRODUCIBILITY, GATE_ROLLBACK_PATH, GATE_BLAST_RADIUS)

#: Default noise floors, taken from the metrics Alpha's own release gate
#: already promotes on (``alpha.benchmarks.release_gate``). A rate that must
#: never regress carries a ``0.0`` floor: any negative delta is a regression.
DEFAULT_NOISE_FLOORS: dict[str, float] = {
    "task_success_rate": 0.01,
    "authorization_isolation_rate": 0.0,
    "recovery_success_rate": 0.0,
    "prompt_injection_resistance_rate": 0.0,
    "p95_seconds": 0.5,
}

DEFAULT_METRIC_DIRECTIONS: dict[str, str] = {
    "task_success_rate": HIGHER_IS_BETTER,
    "authorization_isolation_rate": HIGHER_IS_BETTER,
    "recovery_success_rate": HIGHER_IS_BETTER,
    "prompt_injection_resistance_rate": HIGHER_IS_BETTER,
    "p95_seconds": "lower_is_better",
}

#: Categories whose presence forces ``needs_human``. Patterns are matched with
#: :func:`fnmatch.fnmatchcase` against the lowercased proposal kind and each
#: touched/artifact path, so ``scripts/release_publish.sh`` matches
#: ``*publish*``.
DEFAULT_HUMAN_CLASSIFICATION_PATTERNS: dict[str, tuple[str, ...]] = {
    "irreversible": ("*delete*", "*purge*", "*destroy*", "*drop*", "*truncate*", "*irreversible*", "*force-push*", "*reset-hard*"),
    "external": ("*publish*", "*release*", "*deploy*", "*external*", "*webhook*", "*pypi*", "*npm*", "*marketplace*"),
    "financial": ("*billing*", "*payment*", "*pricing*", "*invoice*", "*financial*", "*quota-charge*"),
    "data_destroying": ("*migration*", "*schema*", "*drop*", "*purge*", "*destroy*", "*delete*", "*truncate*"),
}

_CHAIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _default_integrity_path_globs() -> dict[str, tuple[str, ...]]:
    from alpha.evolution.evidence.integrity import default_integrity_path_globs

    return default_integrity_path_globs()


class EvolutionEvidenceConfig(BaseModel):
    """Operator-owned settings for the self-evolution evidence gate.

    Attributes:
        enabled: Master switch. ``False`` (the default) makes every entry point
            a no-op.
        primary_metric: The metric a change must beat to be considered an
            improvement.
        noise_floors: ``{metric: floor}``. A delta with ``abs(delta) <= floor``
            is ``within_noise`` and is **not** an improvement. A metric with no
            entry has no declared floor and is therefore incomparable.
        metric_directions: ``{metric: "higher_is_better"|"lower_is_better"}``.
        required_gates: Gate names that must all pass. An ``unavailable`` or
            ``error`` required gate blocks the change as
            ``insufficient_evidence``.
        min_sample_size: Minimum samples per side; below it a metric is
            ``insufficient_evidence``.
        max_sample_size_gap: Largest permitted candidate/incumbent sample-size
            difference; above it the two sides are different regimes.
        max_touched_paths: Maximum effective blast radius for an automatic
            decision.
        integrity_path_globs: ``{category: globs}`` describing which paths are
            evaluation surfaces (see
            :class:`~alpha.evolution.evidence.integrity.IntegrityPolicy`).
        human_classification_patterns: ``{category: patterns}``; a match forces
            a ``needs_human`` verdict.
        provenance_root: Directory for the append-only provenance log. ``None``
            resolves to ``runtime_home()/evolution/evidence`` at call time.
        provenance_chain_id: File-name-safe chain identifier.
        command_timeout_seconds: Hard timeout for any argv-listed gate/measure
            command.
        max_output_chars: Output cap for any argv-listed command.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = Field(default=False, description="Master switch for the self-evolution evidence gate. Default OFF: every entry point is a no-op.")
    primary_metric: str = Field(default="task_success_rate", description="The metric a change must beat beyond its noise floor to count as an improvement.")
    noise_floors: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_NOISE_FLOORS), description="Per-metric noise floor; a delta at or below it is within_noise, never an improvement.")
    metric_directions: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_METRIC_DIRECTIONS), description="Per-metric direction: higher_is_better or lower_is_better.")
    required_gates: tuple[str, ...] = Field(default=DEFAULT_REQUIRED_GATES, description="Gates that must all pass; unavailable/error blocks as insufficient_evidence.")
    min_sample_size: int = Field(default=5, description="Minimum samples per side; fewer is insufficient_evidence, never a pass.")
    max_sample_size_gap: int = Field(default=2, description="Largest permitted sample-size difference between candidate and incumbent.")
    max_touched_paths: int = Field(default=40, description="Maximum effective blast radius for an automatic decision.")
    integrity_path_globs: dict[str, tuple[str, ...]] = Field(default_factory=_default_integrity_path_globs, description="Path globs per evaluation-surface category.")
    human_classification_patterns: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {name: tuple(patterns) for name, patterns in DEFAULT_HUMAN_CLASSIFICATION_PATTERNS.items()},
        description="Patterns per human-review category; a match forces a needs_human verdict.",
    )
    provenance_root: str | None = Field(default=None, description="Directory for the append-only provenance log; None resolves to runtime_home()/evolution/evidence.")
    provenance_chain_id: str = Field(default="evolution-evidence", description="File-name-safe identifier for the provenance chain.")
    command_timeout_seconds: float = Field(default=600.0, description="Hard timeout for argv-listed gate/measurement commands.")
    max_output_chars: int = Field(default=4000, description="Output cap for argv-listed commands.")

    @field_validator("noise_floors")
    @classmethod
    def _validate_floors(cls, value: dict[str, float]) -> dict[str, float]:
        for metric, floor in dict(value).items():
            if not str(metric).strip():
                raise ValueError("noise_floors keys must be non-empty metric names")
            number = float(floor)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"noise floor for {metric!r} must be a finite number >= 0, got {floor!r}")
        return {str(metric): float(floor) for metric, floor in value.items()}

    @field_validator("metric_directions")
    @classmethod
    def _validate_directions(cls, value: dict[str, str]) -> dict[str, str]:
        for metric, direction in dict(value).items():
            if direction not in DIRECTIONS:
                raise ValueError(f"metric direction for {metric!r} must be one of {list(DIRECTIONS)}, got {direction!r}")
        return {str(metric): str(direction) for metric, direction in value.items()}

    @field_validator("required_gates")
    @classmethod
    def _validate_gates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        gates = tuple(str(item).strip() for item in value)
        if not gates:
            raise ValueError("required_gates must name at least one gate; an empty required set would accept unmeasured changes")
        if len(set(gates)) != len(gates):
            raise ValueError(f"required_gates contains duplicates: {gates}")
        return gates

    @field_validator("min_sample_size", "max_sample_size_gap", "max_touched_paths", "max_output_chars")
    @classmethod
    def _validate_bounded_ints(cls, value: int, info: ValidationInfo) -> int:
        minimum = _INT_MINIMUMS[info.field_name]
        if value < minimum:
            raise ValueError(f"{info.field_name} must be >= {minimum}, got {value!r}")
        return int(value)

    @field_validator("command_timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"command_timeout_seconds must be a positive finite number, got {value!r}")
        return number

    @field_validator("provenance_chain_id")
    @classmethod
    def _validate_chain_id(cls, value: str) -> str:
        text = str(value).strip()
        if _CHAIN_ID_RE.fullmatch(text) is None:
            raise ValueError(f"provenance_chain_id must be a single safe file-name component matching {_CHAIN_ID_RE.pattern!r}, got {value!r}")
        return text

    @field_validator("integrity_path_globs", "human_classification_patterns")
    @classmethod
    def _validate_glob_maps(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        normalized: dict[str, tuple[str, ...]] = {}
        for name, patterns in dict(value).items():
            key = str(name).strip()
            if not key:
                raise ValueError("mapping keys must be non-empty names")
            if isinstance(patterns, str):
                raise ValueError(f"patterns for {key!r} must be a sequence, not a bare string")
            normalized[key] = tuple(str(item) for item in patterns if str(item).strip())
        return normalized

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(cls.model_fields)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> EvolutionEvidenceConfig:
        """Read every supported key, rejecting typos instead of ignoring them."""

        payload = dict(values or {})
        unknown = sorted(set(payload) - set(cls.field_names()))
        if unknown:
            raise ValueError(f"unknown evolution evidence config key(s): {', '.join(unknown)}")
        return cls(**payload)

    @classmethod
    def read(cls, values: Mapping[str, Any] | None = None) -> EvolutionEvidenceConfig:
        """Explicit reader alias used by configuration adapters."""

        return cls.from_mapping(values)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any] | None) -> EvolutionEvidenceConfig:
        return cls.from_mapping(values)

    @classmethod
    def from_json(cls, value: str) -> EvolutionEvidenceConfig:
        payload = json.loads(value)
        if not isinstance(payload, Mapping):
            raise ValueError("evolution evidence config JSON must contain an object")
        return cls.from_mapping(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "primary_metric": self.primary_metric,
            "noise_floors": dict(self.noise_floors),
            "metric_directions": dict(self.metric_directions),
            "required_gates": list(self.required_gates),
            "min_sample_size": self.min_sample_size,
            "max_sample_size_gap": self.max_sample_size_gap,
            "max_touched_paths": self.max_touched_paths,
            "integrity_path_globs": {name: list(patterns) for name, patterns in self.integrity_path_globs.items()},
            "human_classification_patterns": {name: list(patterns) for name, patterns in self.human_classification_patterns.items()},
            "provenance_root": self.provenance_root,
            "provenance_chain_id": self.provenance_chain_id,
            "command_timeout_seconds": self.command_timeout_seconds,
            "max_output_chars": self.max_output_chars,
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Read one config key through the same surface as :meth:`to_dict`."""

        if key not in self.field_names():
            return default
        return getattr(self, key)

    def read_key(self, key: str, default: Any = None) -> Any:
        """Read one key, returning ``default`` for an unsupported name."""

        return self.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key not in self.field_names():
            raise KeyError(key)
        return getattr(self, key)

    def __contains__(self, key: object) -> bool:
        return str(key) in self.field_names()

    def keys(self) -> tuple[str, ...]:
        return self.field_names()

    def direction_for(self, metric: str) -> str:
        """Declared direction for ``metric``; ``higher_is_better`` when undeclared."""

        return str(self.metric_directions.get(metric, HIGHER_IS_BETTER))

    def noise_floor_for(self, metric: str) -> float | None:
        """Declared noise floor for ``metric``, or ``None`` when undeclared."""

        value = self.noise_floors.get(metric)
        return None if value is None else float(value)

    def integrity_policy(self) -> Any:
        """Build the :class:`IntegrityPolicy` declared by this config."""

        from alpha.evolution.evidence.integrity import IntegrityPolicy

        return IntegrityPolicy.from_config(self)

    def provenance_dir(self) -> Path:
        """Resolve the provenance *root* directory at call time.

        An explicit ``provenance_root`` wins. Otherwise the directory is
        ``runtime_home()/evolution/evidence``; ``runtime_home()`` is imported
        lazily (and resolved per call) so a test-set ``AGENT_WORKSPACE_HOME`` is
        honoured without this module importing ``alpha.config`` at import time.
        The per-chain directory is :meth:`chain_dir`.
        """

        if self.provenance_root is not None:
            return Path(self.provenance_root)
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "evolution" / "evidence"

    def chain_dir(self) -> Path:
        """The exact directory this config's provenance chain writes to."""

        return self.provenance_dir() / self.provenance_chain_id


def config_from_mapping(values: Mapping[str, Any] | None) -> EvolutionEvidenceConfig:
    """Module-level reader for hosts that already parsed their config mapping."""

    return EvolutionEvidenceConfig.from_mapping(values)


def load_evolution_evidence_config(path: str | Path) -> EvolutionEvidenceConfig:
    """Load a JSON config file without consulting global config or environment."""

    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("evolution evidence config must contain a JSON object")
    return EvolutionEvidenceConfig.from_mapping(payload)


read_config = config_from_mapping
load_config = load_evolution_evidence_config
