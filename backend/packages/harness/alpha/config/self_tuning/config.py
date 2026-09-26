"""Default-off configuration for Alpha's self-configuration protocol.

The policy follows the separation-of-duties idea in Google SRE's *Release
Engineering* and *Canarying Releases*: configuration is treated as a production
change, evaluated before exposure, observed in a bounded cohort, and retained
only with evidence.  Martin Fowler's *Feature Toggles (Kill Switches)* also
influences the split between a proposal and permission: the protocol owns a
small allowlist, while authorization and product decisions remain outside it.

This module owns policy only.  It never reads the active file, mutates global
state, starts a loop, or mints an authorization token.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BoundsSource = Literal["registry"]
"""The only source of self-tuning numeric bounds.

Keeping this a closed literal is intentional.  A proposal cannot add a second
bounds file, inherit ambient process limits, or select bounds from untrusted
input and thereby widen its own authority.
"""


#: Mandatory protected prefixes.  ``SelfTuningConfig`` unions these into every
#: instance, so even an invalid/over-broad caller cannot remove a safety
#: boundary.  Reasons are surfaced by :mod:`alpha.config.self_tuning.targets`.
PROTECTED_PATH_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "self_tuning": "the protocol's enablement, bounds, protected list, cooldown, and verification policy are its authority boundary",
        "auth": "authentication policy and identity-provider trust cannot be changed by an automated proposal",
        "authorization": "authorization decisions and fail-closed behavior are outside self-configuration authority",
        "guardrails": "pre-tool authorization and safety guardrails require the normal operator change path",
        "sandbox": "sandbox execution, networking, and host-access policy are protected platform boundaries",
        "acp_agents": "ACP agents can carry automatic permission approval; their authority cannot be widened automatically",
        "approval": "approval gates cannot approve or rewrite their own gate",
        "approvals": "approval gates cannot approve or rewrite their own gate",
        "policy": "policy admission and trust rules require an independent operator decision",
        "risk": "risk policy is part of the independent promotion authority",
        "release_gate": "release-gate thresholds cannot be changed by the candidate being gated",
        "kill_switch": "the emergency kill switch must remain independently controlled",
        "estop": "emergency-stop state must remain independently controlled",
        "memory.l1.provenance_enabled": "disabling L1 provenance would erase the evidence needed to audit or roll back a change",
        "system_one.record_decisions": "decision provenance cannot be disabled by the configuration candidate",
        "system_one.calibration_log_path": "the calibration/audit sink cannot be redirected by the candidate",
        "skill_evolution.auto_promote": "automatic promotion approval policy requires an operator decision",
        "skill_evolution.security_fail_closed": "fail-closed skill moderation cannot be weakened automatically",
        "token_budget": "token-budget enforcement ceilings are hard spend/safety limits",
        "subagents.token_budget": "subagent token-budget enforcement ceilings are hard spend/safety limits",
        "subagents.max_total_per_run": "the recursive delegation ceiling is a safety backstop",
        "loop_detection.hard_limit": "the enforced loop-stop ceiling cannot be raised automatically",
        "loop_detection.tool_freq_hard_limit": "the enforced tool-frequency ceiling cannot be raised automatically",
    }
)
"""Protected dotted-path prefixes and the reason they are refused."""


class VerificationPolicy(BaseModel):
    """Fail-closed health comparison policy.

    A metric is healthy only when it appears in both snapshots and stays within
    ``relative_tolerance`` of its pre-change value in the declared direction.
    Missing baseline or post-change data is *not* interpreted as zero.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_metrics: tuple[str, ...] = Field(
        default=("error_rate", "latency_ms"),
        min_length=1,
        description="Real metrics that must be present in both the pre- and post-change snapshots.",
    )
    higher_is_better: tuple[str, ...] = Field(
        default=(),
        description="Metrics whose non-regression direction is increasing.",
    )
    lower_is_better: tuple[str, ...] = Field(
        default=("error_rate", "latency_ms"),
        description="Metrics whose non-regression direction is decreasing.",
    )
    relative_tolerance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Allowed relative worsening (0 disables tolerance) before a change is rolled back.",
    )

    @field_validator("required_metrics", "higher_is_better", "lower_is_better")
    @classmethod
    def _validate_metric_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject blank/duplicate metric names so evidence cannot be ambiguous."""
        cleaned = tuple(value.strip() for value in values)
        if any(not value for value in cleaned):
            raise ValueError("verification metric names must not be blank")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("verification metric names must not contain duplicates")
        return cleaned

    @model_validator(mode="after")
    def _require_explicit_metric_direction(self) -> VerificationPolicy:
        """Require one explicit direction for every required metric."""
        required = set(self.required_metrics)
        higher = set(self.higher_is_better)
        lower = set(self.lower_is_better)
        if higher & lower:
            raise ValueError(f"metrics cannot be both higher- and lower-is-better: {sorted(higher & lower)}")
        if higher | lower != required:
            missing = sorted(required - (higher | lower))
            extra = sorted((higher | lower) - required)
            raise ValueError(f"every required metric needs one direction; missing={missing}, extra={extra}")
        return self


class SelfTuningConfig(BaseModel):
    """Protocol policy; ``enabled=False`` makes every mutating entry point a no-op."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = Field(
        default=False,
        description="Master switch. False disables proposing, canarying, applying, verification, rollback, and governor decisions.",
    )
    canary_window_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=86_400.0,
        description="Bounded observation window used by the injected canary clock.",
    )
    max_step_ratio: float = Field(
        default=0.10,
        gt=0.0,
        le=1.0,
        description="Maximum governor step as a fraction of a target's allowed floor-to-ceiling span.",
    )
    cooldown_seconds: float = Field(
        default=900.0,
        gt=0.0,
        le=604_800.0,
        description="Minimum injected-clock interval between proposals for the same target.",
    )
    bounds_source: BoundsSource = Field(
        default="registry",
        description="Closed source for target floors/ceilings. Only code-owned registry bounds are accepted.",
    )
    protected_paths: tuple[str, ...] = Field(
        default=tuple(PROTECTED_PATH_REASONS),
        description="Additional protected dotted-path prefixes; mandatory safety prefixes are always unioned in.",
    )
    verification_policy: VerificationPolicy = Field(
        default_factory=VerificationPolicy,
        description="Metric and direction policy used to decide whether health regressed.",
    )

    @field_validator("protected_paths")
    @classmethod
    def _normalize_protected_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize prefixes and union mandatory protections back into the list."""
        normalized: list[str] = []
        for raw in values:
            value = raw.strip().strip(".")
            if not value:
                raise ValueError("protected config paths must not be blank")
            if ".." in value.split(".") or any(character.isspace() for character in value):
                raise ValueError(f"invalid protected config path prefix: {raw!r}")
            normalized.append(value)
        # Insertion order is stable and mandatory entries are never removable.
        return tuple(dict.fromkeys((*normalized, *PROTECTED_PATH_REASONS)))
