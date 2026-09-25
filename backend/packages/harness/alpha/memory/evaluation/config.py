"""Configuration for the opt-in Alpha memory evaluation suite.

The benchmark is deliberately disabled by default.  It is an explicit,
operator/developer measurement rather than a hermetic unit-test side effect.
All threshold values live on this object so a report can show the exact gate
that was applied; no metric silently falls back to a hard-coded release value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal

ThresholdComparator = Literal["min", "max"]


@dataclass(frozen=True)
class EvaluationConfig:
    """Host-injected settings for a memory evaluation run."""

    enabled: bool = False
    cases_dir: str | None = None
    min_extraction_recall: float = 0.80
    min_multi_session_accuracy: float = 0.80
    min_temporal_accuracy: float = 0.80
    min_update_accuracy: float = 1.0
    min_abstention_rate: float = 1.0
    max_contamination_rate: float = 0.0
    baseline_path: str | None = None
    max_cases: int | None = None
    storage_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        for name in (
            "min_extraction_recall",
            "min_multi_session_accuracy",
            "min_temporal_accuracy",
            "min_update_accuracy",
            "min_abstention_rate",
            "max_contamination_rate",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)
        if self.cases_dir is not None:
            object.__setattr__(self, "cases_dir", str(self.cases_dir))
        if self.baseline_path is not None:
            object.__setattr__(self, "baseline_path", str(self.baseline_path))
        if self.storage_path is not None:
            object.__setattr__(self, "storage_path", str(self.storage_path))
        if self.max_cases is not None:
            if isinstance(self.max_cases, bool) or int(self.max_cases) < 1:
                raise ValueError("max_cases must be a positive integer or None")
            object.__setattr__(self, "max_cases", int(self.max_cases))

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(field.name for field in fields(cls))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> EvaluationConfig:
        """Read every supported key, rejecting typos rather than ignoring them."""

        payload = dict(values or {})
        unknown = sorted(set(payload) - set(cls.field_names()))
        if unknown:
            raise ValueError(f"unknown memory evaluation config key(s): {', '.join(unknown)}")
        return cls(**payload)

    @classmethod
    def read(cls, values: Mapping[str, Any] | None = None) -> EvaluationConfig:
        """Explicit reader alias used by configuration adapters."""

        return cls.from_mapping(values)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any] | None) -> EvaluationConfig:
        return cls.from_mapping(values)

    @classmethod
    def from_json(cls, value: str) -> EvaluationConfig:
        payload = json.loads(value)
        if not isinstance(payload, Mapping):
            raise ValueError("evaluation config JSON must contain an object")
        return cls.from_mapping(payload)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.field_names()}

    def read_key(self, key: str, default: Any = None) -> Any:
        """Read one key, returning ``default`` for an unsupported name."""

        return self.get(key, default)

    def get(self, key: str, default: Any = None) -> Any:
        """Read one config key through the same surface as ``to_dict``."""

        if key not in self.field_names():
            return default
        return getattr(self, key)

    def __getitem__(self, key: str) -> Any:
        if key not in self.field_names():
            raise KeyError(key)
        return getattr(self, key)

    def keys(self) -> tuple[str, ...]:
        return self.field_names()

    def threshold_specs(self) -> tuple[tuple[str, ThresholdComparator, float], ...]:
        """Return the configured gates in stable report order."""

        return (
            ("extraction_recall", "min", self.min_extraction_recall),
            ("multi_session_accuracy", "min", self.min_multi_session_accuracy),
            ("temporal_accuracy", "min", self.min_temporal_accuracy),
            ("update_accuracy", "min", self.min_update_accuracy),
            ("abstention_rate", "min", self.min_abstention_rate),
            ("contamination_rate", "max", self.max_contamination_rate),
        )

    def threshold_for(self, metric: str) -> tuple[ThresholdComparator, float] | None:
        for name, comparator, threshold in self.threshold_specs():
            if name == metric:
                return comparator, threshold
        return None

    def cases_path(self) -> Path | None:
        return None if self.cases_dir is None else Path(self.cases_dir)

    def baseline_file(self) -> Path | None:
        return None if self.baseline_path is None else Path(self.baseline_path)

    def storage_dir(self) -> Path | None:
        return None if self.storage_path is None else Path(self.storage_path)


def load_evaluation_config(path: str | Path) -> EvaluationConfig:
    """Load a JSON config file without consulting global config or environment."""

    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("evaluation config must contain a JSON object")
    return EvaluationConfig.from_mapping(payload)


def config_from_mapping(values: Mapping[str, Any] | None) -> EvaluationConfig:
    """Module-level reader for hosts that already parsed their config mapping."""

    return EvaluationConfig.from_mapping(values)


read_config = config_from_mapping
load_config = load_evaluation_config


__all__ = [
    "EvaluationConfig",
    "ThresholdComparator",
    "config_from_mapping",
    "load_config",
    "load_evaluation_config",
    "read_config",
]
