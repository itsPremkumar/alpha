"""Configuration for the default-off memory-utility feedback plane.

The model is intentionally self-contained.  It does not import Alpha's shared
memory configuration, which lets a host inject it in tests or promote it in a
later, separately reviewed central-wiring change.  Every field is consumed by
one of the runtime readers below; ``read_all_keys`` is a diagnostic/reader
contract rather than a second source of defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class UtilityConfig(BaseModel):
    """Settings for observation, scoring, recommendation, and bounded state.

    Thresholds are on a 0..1 utility scale.  ``decay_half_life`` is measured in
    seconds because observations use Unix-like numeric timestamps.  The
    aliases are accepted only at the validation boundary and do not create
    additional configuration keys.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)

    enabled: bool = Field(default=False, description="Master gate; false makes every facade entry point a no-op.")
    decay_half_life: float = Field(
        default=30.0,
        gt=0.0,
        le=10_000_000.0,
        validation_alias=AliasChoices(
            "decay_half_life",
            "half_life",
            "half_life_seconds",
            "decay_half_life_seconds",
        ),
        description="Exponential half-life in seconds for feedback recency.",
    )
    keep_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    demote_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    evict_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    min_calibration_samples: int = Field(default=20, ge=1, le=1_000_000)
    max_observations_per_scope: int = Field(
        default=1_000,
        ge=1,
        le=1_000_000,
        validation_alias=AliasChoices("max_observations_per_scope", "max_records_per_scope"),
    )
    demote_budget_pressure: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "demote_budget_pressure",
            "budget_demote_pressure",
            "budget_demote_threshold",
        ),
    )
    evict_budget_pressure: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "evict_budget_pressure",
            "budget_evict_pressure",
            "budget_evict_threshold",
        ),
    )
    storage_path: str | None = Field(default=None, description="Utility state root; None resolves through runtime home.")

    @field_validator("storage_path", mode="before")
    @classmethod
    def _clean_storage_path(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _validate_order(self) -> UtilityConfig:
        if not self.evict_threshold <= self.demote_threshold <= self.keep_threshold:
            raise ValueError("thresholds must satisfy evict_threshold <= demote_threshold <= keep_threshold")
        if not self.demote_budget_pressure <= self.evict_budget_pressure:
            raise ValueError("budget thresholds must satisfy demote_budget_pressure <= evict_budget_pressure")
        return self

    @property
    def half_life_seconds(self) -> float:
        return self.decay_half_life

    @property
    def decay_half_life_seconds(self) -> float:
        return self.decay_half_life

    @property
    def budget_demote_threshold(self) -> float:
        return self.demote_budget_pressure

    @property
    def budget_evict_threshold(self) -> float:
        return self.evict_budget_pressure

    @property
    def max_records_per_scope(self) -> int:
        return self.max_observations_per_scope

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> UtilityConfig:
        """Validate an injected mapping without consulting global config."""

        return cls.model_validate(dict(values or {}))

    @classmethod
    def read(cls, values: Mapping[str, Any] | None = None) -> UtilityConfig:
        """Explicit reader alias used by host configuration adapters."""

        return cls.from_mapping(values)

    def read_all_keys(self) -> dict[str, Any]:
        """Read every supported setting through one auditable surface."""

        return self.model_dump(mode="python")

    def read_key(self, key: str, default: Any = None) -> Any:
        aliases = {
            "half_life": "decay_half_life",
            "half_life_seconds": "decay_half_life",
            "decay_half_life_seconds": "decay_half_life",
            "max_records_per_scope": "max_observations_per_scope",
            "budget_demote_pressure": "demote_budget_pressure",
            "budget_demote_threshold": "demote_budget_pressure",
            "budget_evict_pressure": "evict_budget_pressure",
            "budget_evict_threshold": "evict_budget_pressure",
        }
        canonical = aliases.get(key, key)
        if canonical not in type(self).model_fields:
            return default
        return getattr(self, canonical)

    def to_dict(self) -> dict[str, Any]:
        return self.read_all_keys()

    def resolved_storage_path(self) -> Path:
        """Resolve the configured root using the L1-compatible path helper."""

        from .paths import utility_root

        return utility_root(self.storage_path)


def config_from_mapping(values: Mapping[str, Any] | None) -> UtilityConfig:
    return UtilityConfig.from_mapping(values)


def read_config(values: Mapping[str, Any] | None = None) -> UtilityConfig:
    return UtilityConfig.read(values)


def load_config(values: Mapping[str, Any] | str | Path | None = None) -> UtilityConfig:
    """Load an injected mapping or a small JSON file without global state."""

    if isinstance(values, (str, Path)):
        import json

        payload = json.loads(Path(values).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("utility config JSON must contain an object")
        return UtilityConfig.from_mapping(payload)
    return UtilityConfig.from_mapping(values)


__all__ = ["UtilityConfig", "config_from_mapping", "load_config", "read_config"]
