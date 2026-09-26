"""Configuration for the opt-in memory health plane.

Every field is consumed by :mod:`alpha.memory.health.health` or one of its
collaborators.  The aliases are intentionally narrow: they make the schema
pleasant to load from existing YAML terminology without creating duplicate
configuration keys or unread settings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AlertSeverity, SLOTarget


class HealthConfig(BaseModel):
    """Settings for the default-off memory health subsystem."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(default=False, description="Master gate; false makes every entry point a no-op.")
    max_components: int = Field(default=64, ge=1, description="Hard cap on registered health probes.")
    freshness_warning_seconds: float = Field(
        default=300.0,
        gt=0,
        validation_alias=AliasChoices("freshness_warning_seconds", "freshness_warn_seconds", "freshness_threshold_seconds"),
        description="Age at which FreshnessProbe reports degraded.",
    )
    freshness_critical_seconds: float = Field(
        default=3600.0,
        gt=0,
        validation_alias=AliasChoices(
            "freshness_critical_seconds",
            "freshness_stale_seconds",
            "freshness_error_seconds",
        ),
        description="Older age disclosed as critical freshness evidence.",
    )
    slo: list[SLOTarget] = Field(
        default_factory=list,
        validation_alias=AliasChoices("slo", "slo_targets"),
        description="Targets evaluated by MemoryHealth.evaluate_slo().",
    )
    metrics_reservoir_size: int = Field(
        default=256,
        ge=1,
        validation_alias=AliasChoices("metrics_reservoir_size", "reservoir_size"),
        description="Maximum retained histogram observations per metric/label set.",
    )
    alert_severities_enabled: list[AlertSeverity] = Field(
        default_factory=lambda: ["info", "warn", "critical"],
        validation_alias=AliasChoices("alert_severities_enabled", "alert_severities"),
        description="Severities that may emit an SLO alert.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Optional health persistence root; None uses the runtime home.",
    )

    @model_validator(mode="before")
    @classmethod
    def _expand_freshness_aliases(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        thresholds = data.pop("freshness_thresholds", None)
        if isinstance(thresholds, dict):
            warning = thresholds.get("warning", thresholds.get("warn"))
            critical = thresholds.get("critical", thresholds.get("error"))
            if "freshness_warning_seconds" not in data and warning is not None:
                data["freshness_warning_seconds"] = warning
            if "freshness_critical_seconds" not in data and critical is not None:
                data["freshness_critical_seconds"] = critical
        return data

    @field_validator("alert_severities_enabled", mode="before")
    @classmethod
    def _normalize_alert_severities(cls, value: object) -> object:
        if value is True:
            return ["info", "warn", "critical"]
        if value is False:
            return []
        return value

    @field_validator("storage_path", mode="before")
    @classmethod
    def _normalize_storage_path(cls, value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        return value

    @model_validator(mode="after")
    def _validate_thresholds(self) -> HealthConfig:
        if self.freshness_critical_seconds < self.freshness_warning_seconds:
            raise ValueError("freshness_critical_seconds must be at least freshness_warning_seconds")
        if len(set(self.alert_severities_enabled)) != len(self.alert_severities_enabled):
            raise ValueError("alert_severities_enabled must not contain duplicates")
        return self

    @property
    def freshness_threshold_seconds(self) -> float:
        """Compatibility alias for the warning threshold."""

        return self.freshness_warning_seconds

    @property
    def freshness_warn_seconds(self) -> float:
        return self.freshness_warning_seconds

    @property
    def freshness_stale_seconds(self) -> float:
        return self.freshness_critical_seconds

    @property
    def alert_severities(self) -> list[str]:
        return list(self.alert_severities_enabled)

    @classmethod
    def read_all_keys(cls) -> frozenset[str]:
        """Return the canonical schema keys for configuration coverage tests."""

        return frozenset(cls.model_fields)


def read_all_keys(config: HealthConfig | None = None) -> frozenset[str]:
    """Return canonical keys for ``config`` (or the default schema)."""

    if config is None:
        return HealthConfig.read_all_keys()
    return frozenset(type(config).model_fields)


__all__ = ["HealthConfig", "read_all_keys"]
