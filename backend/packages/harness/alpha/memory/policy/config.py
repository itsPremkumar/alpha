"""Self-contained, opt-in configuration for memory admission.

This module deliberately does not import the host ``MemoryConfig``.  Central
promotion can therefore import it without creating a configuration cycle, and
embedding applications can inject it directly in tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .scoring import validate_score_weights

DEFAULT_MIN_SCORE_TO_ADMIT = 0.60


class PolicyConfig(BaseModel):
    """Runtime settings for the admission policy engine (default OFF)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(default=False, description="Master admission-policy gate.")
    policy_path: str | None = Field(default=None, description="Explicit YAML path; null searches runtime home then package defaults.")
    strict: bool = Field(default=True, description="Reject unknown policy document fields; unknown rules always fail.")
    min_score_to_admit: float = Field(default=DEFAULT_MIN_SCORE_TO_ADMIT, ge=0.0, le=1.0)
    weights_override: dict[str, float] | None = Field(default=None)
    secret_patterns_enabled: bool = Field(default=True, description="Enable conservative content-pattern secret detection.")
    storage_path: str | None = Field(default=None, description="Root for per-user admission provenance; null uses runtime home.")

    @field_validator("policy_path", "storage_path", mode="before")
    @classmethod
    def _normalize_path(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("weights_override")
    @classmethod
    def _validate_weights(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        return validate_score_weights(value)

    def read_all_keys(self) -> dict[str, Any]:
        """Real reader surface used by diagnostics and contract tests."""

        return {
            "enabled": self.enabled,
            "policy_path": self.policy_path,
            "strict": self.strict,
            "min_score_to_admit": self.min_score_to_admit,
            "weights_override": dict(self.weights_override) if isinstance(self.weights_override, Mapping) else None,
            "secret_patterns_enabled": self.secret_patterns_enabled,
            "storage_path": self.storage_path,
        }


__all__ = ["DEFAULT_MIN_SCORE_TO_ADMIT", "PolicyConfig"]
