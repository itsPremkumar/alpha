"""In-package configuration for Alpha's opt-in entity-memory subsystem.

The subsystem intentionally has its own configuration boundary.  It does not
read or mutate ``alpha.config.memory_config.MemoryConfig``; a host must inject
an :class:`EntityConfig` until the central owner promotes these keys into the
shared configuration in a separately reviewed wiring change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EntityConfig(BaseModel):
    """Entity extraction, linking, retention, and recall settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch for entity capture and recall; disabled by default.",
    )
    max_entities_per_user: int = Field(
        default=500,
        ge=1,
        le=100_000,
        description="Maximum retained entities in one user's entity document.",
    )
    max_aliases_per_entity: int = Field(
        default=12,
        ge=1,
        le=128,
        description="Maximum non-canonical aliases retained per entity.",
    )
    min_mentions_to_promote: int = Field(
        default=1,
        ge=1,
        le=1_000_000,
        description="Mention count required before default entity recall exposes a node.",
    )
    extraction_model: str | None = Field(
        default=None,
        description="Optional host-selected enrichment model name; deterministic extraction always runs first.",
    )
    enable_model_extraction: bool = Field(
        default=False,
        description="Allow optional model enrichment after deterministic extraction.",
    )
    merge_similarity_threshold: float = Field(
        default=0.86,
        ge=0.0,
        le=1.0,
        description="Minimum deterministic name-similarity score for an implicit merge.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Entity state root; None resolves to the Alpha runtime home via l1_root().",
    )

    @field_validator("storage_path")
    @classmethod
    def _validate_storage_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("storage_path must be None or a non-blank path")
        return cleaned

    @field_validator("extraction_model")
    @classmethod
    def _validate_model_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("extraction_model must be None or a non-blank model name")
        return cleaned


def entities_enabled(config: EntityConfig | Mapping[str, Any] | None = None) -> bool:
    """Resolve only this package's explicit gate without reading MemoryConfig."""
    if config is None:
        return False
    if isinstance(config, Mapping):
        return bool(EntityConfig.model_validate(config).enabled)
    return bool(config.enabled)


def resolve_entity_config(values: Mapping[str, Any] | None = None) -> EntityConfig:
    """Validate host-supplied overrides into a self-contained config object."""
    return EntityConfig.model_validate(dict(values or {}))


__all__ = ["EntityConfig", "entities_enabled", "resolve_entity_config"]
