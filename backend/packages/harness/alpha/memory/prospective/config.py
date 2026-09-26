"""Configuration and runtime-home resolution for prospective memory.

The package intentionally owns its configuration instead of importing the
host-wide ``MemoryConfig``.  That keeps the new memory type self-contained and
lets a caller inject an explicit config in tests or an embedding application.
A small YAML override may live below the existing Alpha runtime home; no new
environment variable is introduced.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from alpha.agents.memory.l1.paths import l1_root


class ProspectiveConfigError(ValueError):
    """Raised when a prospective-memory override cannot be loaded honestly."""


class ProspectiveConfig(BaseModel):
    """Bounded policy for the prospective-memory subsystem.

    ``enabled`` defaults to false.  The central wiring can explicitly load or
    construct an enabled config without changing the behavior of existing
    Alpha installations.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Master switch for prospective memory.")
    default_priority: int = Field(default=50, ge=-1, le=100)
    expiry_grace_hours: float = Field(default=24.0, ge=0.0)
    max_pending_per_user: int = Field(default=100, ge=1)
    max_surfaced_per_recall: int = Field(default=5, ge=1)
    max_items_per_user: int = Field(default=1000, ge=1)
    recurring_max_occurrences: int = Field(default=12, ge=1)
    storage_path: str | None = None

    @field_validator("storage_path", mode="before")
    @classmethod
    def _normalize_storage_path(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @property
    def resolved_storage_path(self) -> Path:
        """Resolve the configured root, falling back to Alpha runtime home."""

        return prospective_root(self.storage_path)


@dataclass(slots=True)
class ConfigLoadResult:
    """Outcome of loading defaults or an override file."""

    config: ProspectiveConfig
    status: str
    source: Path | None = None
    reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when a valid config was produced."""

        return self.status in {"defaults", "loaded"}


def prospective_root(storage_path: str | Path | None = None) -> Path:
    """Resolve a prospective state root using Alpha's existing path helper."""

    if storage_path is not None:
        return l1_root(str(storage_path))
    return l1_root(None)


def _candidate_override_paths(root: Path) -> tuple[Path, ...]:
    """Return supported, deterministic override locations below ``root``."""

    return (
        root / "prospective-memory.yaml",
        root / "memory-prospective.yaml",
        root / "memory" / "prospective.yaml",
        root / "memory" / "prospective" / "config.yaml",
        root / "config" / "prospective-memory.yaml",
        root / "config" / "prospective.yaml",
    )


def _find_override(root: Path, explicit_path: str | Path | None) -> Path | None:
    if explicit_path is not None:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_dir():
            return next((path for path in _candidate_override_paths(candidate) if path.is_file()), None)
        return candidate
    return next((path for path in _candidate_override_paths(root) if path.is_file()), None)


def _read_override(path: Path) -> ProspectiveConfig:
    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw_text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProspectiveConfigError(f"could not read prospective config {path}: {exc}") from exc
    if payload is None:
        return ProspectiveConfig()
    if not isinstance(payload, Mapping):
        raise ProspectiveConfigError(f"prospective config {path} must contain a mapping")
    try:
        return ProspectiveConfig.model_validate(dict(payload))
    except ValidationError as exc:
        raise ProspectiveConfigError(f"invalid prospective config {path}: {exc}") from exc


def load_prospective_config_result(
    override_path: str | Path | None = None,
    *,
    storage_path: str | Path | None = None,
) -> ConfigLoadResult:
    """Load config and retain an explicit status/reason for status surfaces."""

    root = prospective_root(storage_path)
    path = _find_override(root, override_path)
    if path is None and storage_path is not None and override_path is None:
        path = _find_override(prospective_root(None), None)
    if path is None:
        return ConfigLoadResult(
            config=ProspectiveConfig(storage_path=str(storage_path) if storage_path is not None else None),
            status="defaults",
            reason="no_override_file",
        )
    try:
        config = _read_override(path)
    except ProspectiveConfigError as exc:
        return ConfigLoadResult(
            config=ProspectiveConfig(storage_path=str(storage_path) if storage_path is not None else None),
            status="failed",
            source=path,
            reason="override_invalid",
            error=str(exc),
        )
    if config.storage_path is None and storage_path is not None:
        config = config.model_copy(update={"storage_path": str(storage_path)})
    return ConfigLoadResult(config=config, status="loaded", source=path)


def load_prospective_config(
    override_path: str | Path | None = None,
    *,
    storage_path: str | Path | None = None,
) -> ProspectiveConfig:
    """Load defaults plus an optional override, failing loudly on bad input.

    A missing override is normal and returns the in-package defaults.  An
    unreadable or schema-invalid override raises :class:`ProspectiveConfigError`
    rather than silently switching to a different policy.
    """

    result = load_prospective_config_result(override_path, storage_path=storage_path)
    if result.status == "failed":
        raise ProspectiveConfigError(result.error or result.reason)
    return result.config


def prospective_enabled(
    config: ProspectiveConfig | Mapping[str, Any] | None = None,
    *,
    master_enabled: bool = True,
) -> bool:
    """Return the two-level gate used by all public store/recall operations."""

    if not master_enabled:
        return False
    if config is None:
        config = load_prospective_config()
    elif isinstance(config, Mapping):
        config = ProspectiveConfig.model_validate(config)
    return bool(config.enabled)


__all__ = [
    "ConfigLoadResult",
    "ProspectiveConfig",
    "ProspectiveConfigError",
    "load_prospective_config",
    "load_prospective_config_result",
    "prospective_enabled",
    "prospective_root",
]
