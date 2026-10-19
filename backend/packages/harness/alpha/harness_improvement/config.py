"""Harness improvement configuration (`config.yaml -> harness_improvement`).

The operator surface for Alpha's recursive harness improvement subsystem
(``docs/ALPHA_HARNESS2_IMPLEMENTATION_GUIDE.md`` §17). The defaults are the
conservative ones from the guide: disabled until an operator turns it on,
one sequential step, one candidate per scope, one concurrent probe, measured
ceilings refused rather than clamped, and a promotion gate that parks for
human approval.

Two properties are load-bearing:

* **The bounds are refusals, not clamps.** A limit below its floor or above
  its ceiling fails config validation with the bound named, because a
  silently-clamped budget is a budget nobody set.
* **This module is the only place these keys are *declared*.** The engine's
  :class:`~alpha.harness_improvement.engine.HarnessImprovementSettings` is
  built from this model's ``model_dump()`` via ``from_mapping``, which
  refuses unknown keys — so a key added here without the engine counterpart
  (or vice versa) fails a test, not a run.

The engine deliberately does not import this module: the harness package
never imports ``alpha.config``'s pydantic models, and the Gateway converts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HarnessImprovementSandboxConfig(BaseModel):
    """Probe isolation posture (guide §17 ``[harness_improvement.sandbox]``)."""

    model_config = ConfigDict(extra="forbid")

    isolate_filesystem: bool = Field(
        default=True,
        description="Every probe runs in its own workspace root; candidates never share mutable files.",
    )
    isolate_memory_writes: bool = Field(
        default=True,
        description="A probe may not write Alpha's durable memory; lessons travel through the evidence-gated experience library only.",
    )
    allow_external_side_effects: bool = Field(
        default=False,
        description="Off = a task or proposal that needs an externally visible side effect is refused before execution. On is an explicit operator opt-in.",
    )
    network_mode: Literal["deny_by_default", "allow"] = Field(
        default="deny_by_default",
        description="Probe network posture, recorded in every ProbeEnvironment; ``allow`` is an explicit operator opt-in and never a candidate's own choice.",
    )
    allow_candidate_middleware_changes: bool = Field(
        default=False,
        description="Off = a proposal that edits middleware components is refused by the patch gate, naming this setting.",
    )
    allow_candidate_guardrail_policy_changes: bool = Field(
        default=False,
        description="Off = a proposal that edits guardrail *guidance* is refused. The enforced policy core (``policy:*`` / ``guardrails:enforced``) stays non-editable regardless: guidance never replaces enforced permissions.",
    )


class HarnessImprovementPromotionConfig(BaseModel):
    """Promotion gate posture (guide §17 ``[harness_improvement.promotion]``)."""

    model_config = ConfigDict(extra="forbid")

    automatic_global_promotion: bool = Field(
        default=False,
        description="Off = every promotion parks for human approval; a run can propose a change, never promote its own change globally.",
    )
    require_regression_suite: bool = Field(
        default=True,
        description="A promotion without a passing regression suite is refused, naming the missing suite.",
    )
    require_human_approval_for_scripts: bool = Field(
        default=True,
        description="Promoting a candidate that adds or edits executable scripts needs an explicit human approval step.",
    )
    require_human_approval_for_middleware: bool = Field(
        default=True,
        description="Promoting a candidate that changes middleware needs an explicit human approval step.",
    )
    require_human_approval_for_permissions: bool = Field(
        default=True,
        description="Promoting a candidate that changes permissions needs an explicit human approval step.",
    )
    keep_last_known_good: bool = Field(
        default=True,
        description="Keep the prior active bundle addressable so rollback always has a target.",
    )


class HarnessImprovementConfig(BaseModel):
    """``config.yaml -> harness_improvement`` settings (guide §17)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Feature flag: off = every harness-improvement surface refuses with the reason named, and nothing is written.",
    )
    mode: Literal["sequential"] = Field(
        default="sequential",
        description="Execution mode. ``sequential`` is the only mode implemented; any other value is refused by name rather than silently degraded.",
    )
    max_steps: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Improvement cycles per run; refused above the ceiling, never clamped.",
    )
    max_candidates_per_scope: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Candidate proposals per scope (general and task/domain); refused above the ceiling, never clamped.",
    )
    max_concurrent_probes: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Probes executable at once; refused above the ceiling, never clamped. Candidate count multiplies cost, so this stays minimal by default.",
    )
    max_wall_time_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Whole-run wall-clock ceiling in seconds; refused outside the bounds, never clamped.",
    )
    max_tool_calls_per_probe: int = Field(
        default=60,
        ge=1,
        le=1000,
        description="Tool calls one probe may spend; refused above the ceiling, never clamped.",
    )
    max_model_turns_per_probe: int = Field(
        default=80,
        ge=1,
        le=1000,
        description="Model turns one probe may spend; refused above the ceiling, never clamped.",
    )
    max_diff_files: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Files one candidate proposal may touch; a bigger diff is refused, never truncated.",
    )
    max_changed_bytes: int = Field(
        default=32768,
        ge=1,
        le=1048576,
        description="Changed bytes one candidate proposal may total; refused above the ceiling, never truncated.",
    )
    max_model_calls: int | None = Field(
        default=24,
        ge=1,
        le=1000,
        description="Measured model-call ceiling for the whole run; ``null`` means unstated, in which case the measurable dimensions are still enforced.",
    )
    max_cost_usd: float | None = Field(
        default=None,
        gt=0.0,
        description="Measured cost ceiling in USD; ``null`` means unstated. A provider that reports no usage leaves the dimension unenforceable, which is reported — never silently treated as under budget.",
    )
    max_experience_entries_in_context: int = Field(
        default=5,
        ge=0,
        le=50,
        description="Bounded number of experience-library entries a later run may read into context; refused above the ceiling, never clamped.",
    )
    min_evidence_for_positive_lesson: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Minimum distinct evidence references a positive lesson needs before it is stored; an unsupported lesson is rejected, not saved.",
    )
    model: str | None = Field(
        default=None,
        description="Names the model from ``models[]`` probes and the successor run use when a real executor is wired; ``null`` keeps the runtime default. The name is validated at config load.",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        description="Fixed seed recorded in every probe environment so a replayed comparison can check matched conditions; ``null`` means unstated.",
    )
    sandbox: HarnessImprovementSandboxConfig = Field(
        default_factory=HarnessImprovementSandboxConfig,
        description="Probe isolation and policy-change posture.",
    )
    promotion: HarnessImprovementPromotionConfig = Field(
        default_factory=HarnessImprovementPromotionConfig,
        description="Promotion ladder gate posture.",
    )


__all__ = [
    "HarnessImprovementConfig",
    "HarnessImprovementPromotionConfig",
    "HarnessImprovementSandboxConfig",
]
