"""Self-contained, opt-in configuration for narrative memory.

The narrative subsystem intentionally has its own configuration boundary.  It
must not read or mutate the host ``MemoryConfig`` while the central owner
reviews how to promote these knobs.  Tests and hosts can inject a validated
``NarrativeConfig`` directly; all policy fields are read by the public seams in
this package.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import narrative_root


class NarrativeConfig(BaseModel):
    """Bounded capture, synthesis, retention, and recall policy."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(
        default=False,
        description="Master switch for narrative capture, regeneration, and recall; off by default.",
    )
    max_events: int = Field(
        default=500,
        ge=1,
        le=100_000,
        description="Maximum retained events in one narrative scope.",
    )
    max_chars: int = Field(
        default=8_000,
        ge=1,
        le=1_000_000,
        description="Maximum rendered characters in one story document.",
    )
    max_chapters: int = Field(
        default=32,
        ge=1,
        le=1_000,
        description="Maximum ordered period chapters retained in one story.",
    )
    min_importance: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="Importance floor used for story eligibility and eviction preference.",
    )
    lookback_days: float = Field(
        default=365.0,
        ge=0.0,
        le=100_000.0,
        description="Maximum age of an event considered by automatic story synthesis.",
    )
    synthesis_model: str | None = Field(
        default=None,
        description="Optional named model for chapter refinement; no external lookup is performed here.",
    )
    enable_model_synthesis: bool = Field(
        default=False,
        description="Allow the injected model seam to refine deterministic chapters.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Narrative state root; None resolves to the Alpha runtime home via l1_root().",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_path_input(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and isinstance(value.get("storage_path"), Path):
            data = dict(value)
            data["storage_path"] = str(data["storage_path"])
            return data
        return value

    @field_validator("storage_path")
    @classmethod
    def _validate_storage_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("storage_path must be None or a non-blank path")
        return cleaned

    @field_validator("synthesis_model")
    @classmethod
    def _validate_synthesis_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("synthesis_model must be None or a non-blank model name")
        return cleaned[:256]

    @property
    def resolved_storage_path(self) -> Path:
        """Resolve the runtime-home fallback without reading shared config."""

        return narrative_root(self.storage_path)

    def read_all_keys(self) -> dict[str, Any]:
        """Expose a real reader for every policy key for diagnostics/tests."""

        return {
            "enabled": self.enabled,
            "max_events": self.max_events,
            "max_chars": self.max_chars,
            "max_chapters": self.max_chapters,
            "min_importance": self.min_importance,
            "lookback_days": self.lookback_days,
            "synthesis_model": self.synthesis_model,
            "enable_model_synthesis": self.enable_model_synthesis,
            "storage_path": self.storage_path,
        }


def narrative_enabled(config: NarrativeConfig | Mapping[str, Any] | None = None) -> bool:
    """Read the package-local gate without consulting shared MemoryConfig."""

    if config is None:
        return False
    if isinstance(config, Mapping):
        return bool(NarrativeConfig.model_validate(config).enabled)
    return bool(config.enabled)


def resolve_narrative_config(values: Mapping[str, Any] | None = None) -> NarrativeConfig:
    """Validate host-supplied overrides into a self-contained config."""

    return NarrativeConfig.model_validate(dict(values or {}))


__all__ = ["NarrativeConfig", "narrative_enabled", "resolve_narrative_config"]
