"""Default-off configuration for the additive reasoning plane.

This module deliberately does **not** import ``alpha.config`` or register an
``AppConfig`` field.  Defaults live with the package.  An operator may place a
small JSON override at ``$AGENT_WORKSPACE_HOME/reasoning/config.json`` or pass
an explicit path; a missing optional file yields defaults and a malformed file
fails loudly.

Loading is uncached and every returned object is instance-scoped, so tests and
concurrent hosts cannot leak configuration into one another.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from alpha.reasoning.budget import BudgetLimits
from alpha.reasoning.loopguard import LoopGuardCaps
from alpha.reasoning.models import ClampedModel, ReasoningMode
from alpha.reasoning.policy import PolicyThresholds
from alpha.reasoning.summary import SummarySettings
from alpha.reasoning.ttcs import TTCSSettings
from alpha.reasoning.uncertainty import UncertaintyThresholds

__all__ = [
    "MAX_REASONING_CONFIG_BYTES",
    "ReasoningConfig",
    "ReasoningConfigError",
    "ReasoningPlaneConfig",
    "is_reasoning_enabled",
    "load_reasoning_config",
    "resolve_reasoning_config_path",
]

MAX_REASONING_CONFIG_BYTES = 64 * 1024
_RUNTIME_RELATIVE_PATH = Path("reasoning") / "config.json"


class ReasoningConfigError(ValueError):
    """The optional reasoning override is present but invalid."""


class ReasoningConfig(ClampedModel):
    """Reasoning-plane settings; disabled unless a host explicitly enables it."""

    enabled: bool = False
    default_mode: ReasoningMode = ReasoningMode.ROUTED
    policy_thresholds: PolicyThresholds = Field(default_factory=PolicyThresholds)
    budget_defaults: BudgetLimits = Field(default_factory=BudgetLimits)
    ttcs: TTCSSettings = Field(default_factory=TTCSSettings)
    uncertainty_enabled: bool = False
    uncertainty_thresholds: UncertaintyThresholds = Field(default_factory=UncertaintyThresholds)
    uncertainty_require_calibration_for_stop: bool = True
    summary: SummarySettings = Field(default_factory=SummarySettings)
    loop_guard: LoopGuardCaps = Field(default_factory=LoopGuardCaps)
    max_atoms: int = Field(default=12)
    max_depth: int = Field(default=4)
    max_width: int = Field(default=6)
    storage_path: Path | None = None

    @model_validator(mode="after")
    def _clamp_atom_limits(self) -> Self:
        self.clamp_field("max_atoms", 1, 1000, integer=True)
        self.clamp_field("max_depth", 0, 100, integer=True)
        self.clamp_field("max_width", 1, 1000, integer=True)
        return self


#: Unambiguous package-root name for the new config.  The legacy root export
#: ``alpha.reasoning.ReasoningConfig`` remains the governor dataclass.
ReasoningPlaneConfig = ReasoningConfig


def resolve_reasoning_config_path(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve the optional override without touching the shared config layer.

    An explicit path is an operator assertion and must exist.  A configured
    runtime home is optional: when its reasoning file is absent, defaults are
    returned by :func:`load_reasoning_config`.
    """

    if path is not None:
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.is_file():
            raise ReasoningConfigError(f"explicit reasoning config path does not exist: {resolved}")
        return resolved
    environment = os.environ if env is None else env
    home = (environment.get("AGENT_WORKSPACE_HOME") or "").strip()
    if not home:
        return None
    candidate = Path(home).expanduser() / _RUNTIME_RELATIVE_PATH
    return candidate.resolve(strict=False) if candidate.is_file() else None


def load_reasoning_config(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> ReasoningConfig:
    """Load defaults plus an optional uncached JSON override."""

    resolved_path = resolve_reasoning_config_path(path, env=env)
    if resolved_path is None:
        return ReasoningConfig()
    try:
        raw_bytes = resolved_path.read_bytes()
    except OSError as exc:
        raise ReasoningConfigError(f"cannot read reasoning config {resolved_path}: {exc}") from exc
    if len(raw_bytes) > MAX_REASONING_CONFIG_BYTES:
        raise ReasoningConfigError(f"reasoning config {resolved_path} is {len(raw_bytes)} bytes; limit is {MAX_REASONING_CONFIG_BYTES}")
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReasoningConfigError(f"reasoning config {resolved_path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReasoningConfigError(f"reasoning config {resolved_path} must contain a JSON object")
    try:
        config = ReasoningConfig.model_validate(payload)
    except ValueError as exc:
        raise ReasoningConfigError(f"reasoning config {resolved_path} is invalid: {exc}") from exc
    if config.storage_path is not None and not config.storage_path.is_absolute():
        config = config.model_copy(update={"storage_path": (resolved_path.parent / config.storage_path).resolve(strict=False)})
    return config


def is_reasoning_enabled(config: ReasoningConfig | None = None) -> bool:
    """Return the explicit host-facing enable gate."""

    return bool((config or ReasoningConfig()).enabled)
