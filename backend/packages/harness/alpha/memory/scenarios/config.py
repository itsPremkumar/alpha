"""Self-contained configuration for scenario-conditioned recall.

This package intentionally does not import the host ``MemoryConfig``.  The
router is an additive memory seam and can be enabled, disabled, or tested with
an explicit config without changing the existing memory schema.  Optional YAML
overrides live below the existing Alpha runtime home and use no new environment
variable.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from alpha.agents.memory.l1.paths import l1_root

from .models import DEFAULT_SCENARIO, Scenario, parse_scenario


class ScenarioConfigError(ValueError):
    """Raised when a scenario configuration override is invalid."""


def scenario_root(storage_path: str | Path | None = None) -> Path:
    """Resolve scenario state using Alpha's existing runtime-home helper."""

    return l1_root(str(storage_path) if storage_path is not None else None)


def _bounded_float(value: Any, low: float, high: float, default: float) -> tuple[float, bool]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default, True
    if not math.isfinite(number):
        return default, True
    result = max(low, min(high, number))
    return result, result != number


def _bounded_int(value: Any, low: int, default: int) -> tuple[int, bool]:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default, True
    result = max(low, number)
    return result, result != number


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, (list, tuple, dict, set)):
        raise ValueError("boolean policy values must be scalar")
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0", ""}:
        return False
    raise ValueError(f"invalid boolean policy value: {value!r}")


class ScenarioConfig(BaseModel):
    """Bounded policy for the scenario router.

    Every field is read by a public seam in this package.  In particular,
    ``enabled`` is checked before classification, ``classifier_model`` and
    ``enable_model_classifier`` gate the injected model path, confidence and
    budget fields are consumed by the classifier/router, freeze fields by
    thread planning, and ``storage_path`` by provenance.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(default=False, description="Master gate; disabled by default.")
    default_scenario: Scenario = Field(default=DEFAULT_SCENARIO, description="Safe fallback scenario.")
    classifier_model: str | None = Field(default=None, description="Optional model name for the injected seam.")
    enable_model_classifier: bool = Field(default=False, description="Whether the optional model seam may run.")
    min_confidence: float = Field(default=0.55, description="Minimum confidence for a non-general label.")
    total_budget_units: int = Field(default=12, description="Maximum summed surface cost per plan.")
    max_surfaces: int = Field(default=8, description="Maximum number of selected surfaces per plan.")
    session_freeze: bool = Field(default=True, description="Freeze a plan for the first thread decision.")
    override_env_freeze: bool = Field(default=True, description="Allow a valid explicit override to replace a frozen plan.")
    storage_path: str | None = Field(default=None, description="Root for per-user scenario provenance.")

    _disclosures: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_config(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        aliases = {
            "model_classifier": "classifier_model",
            "model": "classifier_model",
            "model_enabled": "enable_model_classifier",
            "confidence_threshold": "min_confidence",
            "budget_units": "total_budget_units",
            "surface_limit": "max_surfaces",
            "freeze": "session_freeze",
            "allow_override": "override_env_freeze",
        }
        for source, target in aliases.items():
            if source in data:
                if target not in data:
                    data[target] = data[source]
                data.pop(source, None)
        parsed = parse_scenario(data.get("default_scenario", DEFAULT_SCENARIO), default=None)
        if parsed is None:
            parsed = DEFAULT_SCENARIO
        data["default_scenario"] = parsed

        confidence, _ = _bounded_float(data.get("min_confidence", 0.55), 0.0, 1.0, 0.55)
        data["min_confidence"] = confidence

        budget, _ = _bounded_int(data.get("total_budget_units", 12), 0, 12)
        data["total_budget_units"] = budget

        max_surfaces, _ = _bounded_int(data.get("max_surfaces", 8), 1, 8)
        data["max_surfaces"] = max_surfaces

        data["enabled"] = _as_bool(data.get("enabled", False))
        data["enable_model_classifier"] = _as_bool(data.get("enable_model_classifier", False))
        data["session_freeze"] = _as_bool(data.get("session_freeze", True))
        data["override_env_freeze"] = _as_bool(data.get("override_env_freeze", True))
        model = data.get("classifier_model")
        if model is not None:
            model_text = str(model).strip()
            data["classifier_model"] = model_text[:256] or None
        else:
            data["classifier_model"] = None
        storage = data.get("storage_path")
        data["storage_path"] = str(storage).strip() if storage is not None and str(storage).strip() else None
        return data

    @model_validator(mode="wrap")
    @classmethod
    def _record_normalization(cls, value: Any, handler: Any) -> Any:
        raw = dict(value) if isinstance(value, Mapping) else value
        result = handler(value)
        if not isinstance(result, cls) or not isinstance(raw, Mapping):
            return result
        notices: list[str] = []
        default_raw = raw.get("default_scenario", DEFAULT_SCENARIO)
        if parse_scenario(default_raw, default=None) is None:
            notices.append("unknown default_scenario replaced with general")
        for key, aliases in {
            "min_confidence": ("confidence_threshold",),
            "total_budget_units": ("budget_units",),
            "max_surfaces": ("surface_limit",),
        }.items():
            source = next((name for name in (key, *aliases) if name in raw), None)
            if source is None:
                continue
            try:
                original = float(raw[source])
                effective = float(getattr(result, key))
            except (TypeError, ValueError, OverflowError):
                continue
            if effective != original:
                notices.append(f"{key} clamped to its configured bounds")
        object.__setattr__(result, "_disclosures", _merge_disclosures(notices))
        return result

    @field_validator("min_confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        return _bounded_float(value, 0.0, 1.0, 0.55)[0]

    @field_validator("total_budget_units", mode="before")
    @classmethod
    def _clamp_budget(cls, value: Any) -> int:
        return _bounded_int(value, 0, 12)[0]

    @field_validator("max_surfaces", mode="before")
    @classmethod
    def _clamp_surface_limit(cls, value: Any) -> int:
        return _bounded_int(value, 1, 8)[0]

    @field_validator("enabled", "enable_model_classifier", "session_freeze", "override_env_freeze", mode="before")
    @classmethod
    def _validate_bool(cls, value: Any) -> bool:
        return _as_bool(value)

    @property
    def disclosures(self) -> list[str]:
        return list(self._disclosures)

    @property
    def clamp_disclosure(self) -> str:
        return "none" if not self._disclosures else "; ".join(self._disclosures)

    @property
    def resolved_storage_path(self) -> Path:
        """Resolve provenance storage without consulting ``MemoryConfig``."""

        return scenario_root(self.storage_path)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def read_all_keys(self) -> dict[str, Any]:
        """Return every public policy field for status/reader surfaces.

        This is intentionally a real reader rather than a test-only field
        enumeration: operators can inspect the effective policy without
        knowing which router method consumes each key.
        """

        return {
            "enabled": self.enabled,
            "default_scenario": self.default_scenario.value,
            "classifier_model": self.classifier_model,
            "enable_model_classifier": self.enable_model_classifier,
            "min_confidence": self.min_confidence,
            "total_budget_units": self.total_budget_units,
            "max_surfaces": self.max_surfaces,
            "session_freeze": self.session_freeze,
            "override_env_freeze": self.override_env_freeze,
            "storage_path": self.storage_path,
        }


@dataclass(slots=True)
class ConfigLoadResult:
    """Outcome of loading defaults or an optional local override."""

    config: ScenarioConfig
    status: str
    source: Path | None = None
    reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"defaults", "loaded"}


def _candidate_override_paths(root: Path) -> tuple[Path, ...]:
    """Return deterministic, package-owned override locations."""

    return (
        root / "scenario-memory.yaml",
        root / "scenario-memory.yml",
        root / "memory-scenarios.yaml",
        root / "memory-scenarios.yml",
        root / "memory" / "scenarios.yaml",
        root / "memory" / "scenarios.yml",
        root / "memory" / "scenario.yaml",
        root / "config" / "scenario-memory.yaml",
        root / "config" / "scenarios.yaml",
    )


def _find_override(root: Path, explicit_path: str | Path | None) -> Path | None:
    if explicit_path is not None:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_dir():
            return next((path for path in _candidate_override_paths(candidate) if path.is_file()), None)
        return candidate
    return next((path for path in _candidate_override_paths(root) if path.is_file()), None)


def _read_override(path: Path) -> ScenarioConfig:
    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw_text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ScenarioConfigError(f"could not read scenario config {path}: {exc}") from exc
    if payload is None:
        return ScenarioConfig()
    if not isinstance(payload, Mapping):
        raise ScenarioConfigError(f"scenario config {path} must contain a mapping")
    data = dict(payload)
    # Permit a namespaced file without making the public config object carry a
    # wrapper field.  A flat file remains the documented form.
    nested = data.get("scenarios")
    if isinstance(nested, Mapping):
        data = dict(nested)
    elif isinstance(data.get("memory"), Mapping) and isinstance(data["memory"].get("scenarios"), Mapping):
        data = dict(data["memory"]["scenarios"])
    try:
        return ScenarioConfig.model_validate(data)
    except ValidationError as exc:
        raise ScenarioConfigError(f"invalid scenario config {path}: {exc}") from exc


def load_scenario_config_result(
    override_path: str | Path | None = None,
    *,
    storage_path: str | Path | None = None,
) -> ConfigLoadResult:
    """Load a config while retaining an honest status for status endpoints."""

    root = scenario_root(storage_path)
    path = _find_override(root, override_path)
    if path is None:
        return ConfigLoadResult(
            config=ScenarioConfig(storage_path=str(storage_path) if storage_path is not None else None),
            status="defaults",
            reason="no_override_file",
        )
    try:
        config = _read_override(path)
    except ScenarioConfigError as exc:
        return ConfigLoadResult(
            config=ScenarioConfig(storage_path=str(storage_path) if storage_path is not None else None),
            status="failed",
            source=path,
            reason="override_invalid",
            error=str(exc),
        )
    if config.storage_path is None and storage_path is not None:
        config = config.model_copy(update={"storage_path": str(storage_path)})
    return ConfigLoadResult(config=config, status="loaded", source=path, reason="override_loaded")


def load_scenario_config(
    override_path: str | Path | None = None,
    *,
    storage_path: str | Path | None = None,
) -> ScenarioConfig:
    """Load defaults plus an optional override, failing loudly on bad input."""

    result = load_scenario_config_result(override_path, storage_path=storage_path)
    if result.status == "failed":
        raise ScenarioConfigError(result.error or result.reason)
    return result.config


def scenario_enabled(
    config: ScenarioConfig | Mapping[str, Any] | None = None,
    *,
    master_enabled: bool = True,
) -> bool:
    """Return the package-local gate without importing shared memory config."""

    if not master_enabled:
        return False
    if config is None:
        config = load_scenario_config()
    elif isinstance(config, Mapping):
        config = ScenarioConfig.model_validate(config)
    return bool(config.enabled)


# Plural spelling is convenient at wiring call sites.
scenarios_enabled = scenario_enabled


def _merge_disclosures(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


__all__ = [
    "ConfigLoadResult",
    "ScenarioConfig",
    "ScenarioConfigError",
    "load_scenario_config",
    "load_scenario_config_result",
    "scenario_enabled",
    "scenario_root",
    "scenarios_enabled",
]
