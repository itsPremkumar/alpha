"""Default-off configuration for the StoreKit persistence primitives.

The kit is deliberately not wired into Alpha's global config in this change.
A host opts in by constructing :class:`StoreKitConfig` (or passing it to a
:class:`~alpha.persistence.storekit.store.ScopedStore`) and setting
``enabled=True``.  Every field has an explicit reader, both for diagnostics
and so a reader test can prove that a newly added key is not dead
configuration.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .atomic import FsyncPolicy, normalize_fsync_policy

__all__ = [
    "StoreKitConfig",
    "load_store_kit_config",
    "store_kit_enabled",
]


class StoreKitConfig(BaseModel):
    """Self-contained, default-off settings for durable store operations."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(default=False, description="Master opt-in for StoreKit-backed store operations.")
    locking_enabled: bool = Field(default=True, description="Use the host OS file-lock backend when available.")
    require_lock: bool = Field(default=True, description="Refuse writes when a required lock cannot be acquired.")
    lock_timeout_seconds: float = Field(default=2.0, ge=0.0, le=300.0, description="Bounded wait for a file lock.")
    stale_lock_after_seconds: float = Field(default=300.0, ge=0.0, le=86_400.0, description="Age after which a demonstrably dead lock owner is reported stale.")
    lock_poll_interval_seconds: float = Field(default=0.01, gt=0.0, le=1.0, description="Bounded polling interval while waiting for a lock.")
    allow_shared_reads: bool = Field(default=True, description="Request shared read locks where the host supports them.")
    fsync_policy: FsyncPolicy = Field(default=FsyncPolicy.FILE_AND_DIRECTORY, description="File and directory durability policy for atomic writes.")
    default_target_format_version: int = Field(default=1, ge=1, description="Format version targeted by a store when no caller target is supplied.")
    default_schema_id: str = Field(default="storekit.document", min_length=1, description="Schema id used when a store does not supply one.")
    default_max_documents_per_scope: int = Field(default=1_000, ge=1, le=10_000_000, description="Default record bound for one scope.")
    default_max_bytes_per_scope: int | None = Field(default=None, gt=0, description="Optional byte bound for one scope.")
    retention_keep_ratio: float = Field(default=0.8, gt=0.0, le=1.0, description="Fraction of a byte budget targeted for retention before demotion/eviction.")
    retention_demote_age_seconds: float | None = Field(default=None, ge=0.0, description="Optional age at which the policy proposes demotion.")
    dry_run: bool = Field(default=True, description="Default dry-run mode for migrations; normal store writes are unaffected.")
    preserve_corrupt: bool = Field(default=True, description="Quarantine corrupt documents instead of deleting or overwriting them.")
    json_indent: int | None = Field(default=1, ge=0, le=8, description="JSON indentation used for persisted envelopes; None is compact.")

    @field_validator("lock_timeout_seconds", "stale_lock_after_seconds", "lock_poll_interval_seconds", "retention_keep_ratio", "default_max_bytes_per_scope", "retention_demote_age_seconds", mode="before")
    @classmethod
    def _finite_numbers(cls, value: Any) -> Any:
        if value is None:
            return value
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        if not math.isfinite(number):
            raise ValueError("StoreKit numeric settings must be finite")
        return value

    @field_validator("fsync_policy", mode="before")
    @classmethod
    def _normalize_fsync(cls, value: Any) -> FsyncPolicy:
        return normalize_fsync_policy(value)

    @field_validator("default_schema_id")
    @classmethod
    def _non_blank_schema(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("default_schema_id must not be blank")
        return cleaned

    def read_key(self, name: str) -> Any:
        """Read one declared key explicitly; unknown keys fail loudly."""

        if name not in type(self).model_fields:
            raise KeyError(f"unknown StoreKitConfig key: {name!r}")
        return getattr(self, name)

    def read_all_keys(self) -> dict[str, Any]:
        """Return every declared key through explicit field reads."""

        return {name: self.read_key(name) for name in sorted(type(self).model_fields)}

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(type(self).model_fields))

    def __getitem__(self, name: str) -> Any:
        return self.read_key(name)

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return self.read_key(name)
        except KeyError:
            return default

    def to_dict(self) -> dict[str, Any]:
        return self.read_all_keys()

    # Compatibility spellings for hosts that used the shorter vocabulary in
    # an early prototype.  They are properties, not duplicate persisted keys.
    @property
    def lock_timeout(self) -> float:
        return self.lock_timeout_seconds

    @property
    def stale_lock_threshold(self) -> float:
        return self.stale_lock_after_seconds

    @property
    def target_format_version(self) -> int:
        return self.default_target_format_version

    @property
    def max_documents_per_scope(self) -> int:
        return self.default_max_documents_per_scope

    @property
    def max_bytes_per_scope(self) -> int | None:
        return self.default_max_bytes_per_scope

    @property
    def default_dry_run(self) -> bool:
        return self.dry_run


def load_store_kit_config(values: Mapping[str, Any] | None = None, **overrides: Any) -> StoreKitConfig:
    """Load defaults plus explicit host overrides without a global singleton."""

    payload: dict[str, Any] = dict(values or {})
    payload.update(overrides)
    return StoreKitConfig.model_validate(payload)


def store_kit_enabled(config: StoreKitConfig | Mapping[str, Any] | None = None) -> bool:
    """Read the package-local opt-in gate, defaulting closed."""

    if config is None:
        return False
    if isinstance(config, Mapping):
        return bool(StoreKitConfig.model_validate(config).enabled)
    return bool(config.enabled)
