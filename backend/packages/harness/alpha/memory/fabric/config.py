"""Self-contained, default-off configuration for Alpha's memory fabric."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# `l1_root` is imported LAZILY inside the methods that use it, not here at
# module scope.
#
# Why: a module-scope `from alpha.agents.memory.l1.paths import l1_root` makes
# importing this config file execute the `alpha.agents` package __init__, which
# imports `.features`, which imports `langchain.agents`. Measured with
# `-X importtime` on 2026-10-05: importing `alpha.config.app_config` (which
# reaches this file) took 86 s, of which ~59 s was `langchain.agents` and its
# transitive dependencies — dragged in by a config module for the sake of one
# path helper. That is long enough that the gateway's startup watchdog kills the
# process before it finishes importing, and it fails silently (empty logs,
# nothing bound to the port).
#
# `paths.py` itself has no heavy dependencies (re, pathlib, and a function-local
# import), so deferring the cost until first use is free. Both call sites below
# are inside methods, never at import time.


class FabricConfig(BaseModel):
    """Admission, decay, retention, and storage policy for memory envelopes."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(
        default=False,
        description="Master switch for fabric admission, transition, recall, and deletion.",
    )
    default_decay_rate: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Default per-envelope decay pressure applied by the create seam.",
    )
    default_ttl_seconds: float | None = Field(
        default=None,
        gt=0.0,
        description="Default retention TTL in seconds; None leaves expiry unbounded.",
    )
    max_tags: int = Field(
        default=64,
        ge=1,
        le=10_000,
        description="Maximum tags accepted on one canonical envelope.",
    )
    purge_after_days: float = Field(
        default=3650.0,
        ge=0.0,
        description="Age at which a record becomes due for promotion to PURGED.",
    )
    archive_after_days: float = Field(
        default=365.0,
        ge=0.0,
        description="Age at which a record becomes due for promotion to ARCHIVED.",
    )
    compress_after_days: float = Field(
        default=30.0,
        ge=0.0,
        description="Age at which a record becomes due for promotion to COMPRESSED.",
    )
    allow_secret_classification: bool = Field(
        default=False,
        description="Explicitly permit secret-labelled envelopes; false is fail-closed.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Fabric state root; None resolves through the L1 runtime-home helper.",
    )

    @field_validator("default_ttl_seconds")
    @classmethod
    def _finite_ttl(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(float(value)):
            raise ValueError("default_ttl_seconds must be finite")
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

    @model_validator(mode="after")
    def _ordered_age_thresholds(self) -> FabricConfig:
        if self.compress_after_days > self.archive_after_days:
            raise ValueError("compress_after_days must not exceed archive_after_days")
        if self.archive_after_days > self.purge_after_days:
            raise ValueError("archive_after_days must not exceed purge_after_days")
        return self

    def resolved_root(self) -> Path:
        """Resolve the fabric root using the same runtime-home rule as L1."""

        from alpha.agents.memory.l1.paths import l1_root

        return l1_root(self.storage_path)

    def read_all_keys(self) -> dict[str, Any]:
        """Return every key through an explicit reader for diagnostics/tests."""

        return {
            "enabled": self.enabled,
            "default_decay_rate": self.default_decay_rate,
            "default_ttl_seconds": self.default_ttl_seconds,
            "max_tags": self.max_tags,
            "purge_after_days": self.purge_after_days,
            "archive_after_days": self.archive_after_days,
            "compress_after_days": self.compress_after_days,
            "allow_secret_classification": self.allow_secret_classification,
            "storage_path": self.storage_path,
        }


def load_fabric_config(
    values: Mapping[str, Any] | None = None,
    *,
    storage_path: str | Path | None = None,
) -> FabricConfig:
    """Load package defaults plus explicit host overrides without a singleton."""

    payload = dict(values or {})
    if storage_path is not None:
        payload.setdefault("storage_path", str(storage_path))
    return FabricConfig.model_validate(payload)


def fabric_enabled(config: FabricConfig | Mapping[str, Any] | None = None) -> bool:
    """Read the package-local enable gate, defaulting closed."""

    if config is None:
        return False
    if isinstance(config, Mapping):
        return bool(FabricConfig.model_validate(config).enabled)
    return bool(config.enabled)


def fabric_root(storage_path: str | Path | None = None) -> Path:
    """Resolve a fabric root, mirroring ``l1.paths.l1_root`` exactly."""

    from alpha.agents.memory.l1.paths import l1_root

    return l1_root(str(storage_path) if storage_path is not None else None)


__all__ = [
    "FabricConfig",
    "fabric_enabled",
    "fabric_root",
    "load_fabric_config",
]
