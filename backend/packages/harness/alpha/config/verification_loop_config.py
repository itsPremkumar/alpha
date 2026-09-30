"""``config.yaml -> verification_loop`` — policy for the bounded verification loop.

Owned by `alpha.verification` (`packages/harness/alpha/verification/AGENTS.md`),
which is the component that *drives* run -> read failure -> fix -> re-run. This
model validates operator input for it; the controller itself is a
dependency-free policy object so a loop can never be left unconfigured by a
pydantic import failure.

Startup-only, like ``network`` and ``run_ownership``: a loop already in flight
holds a budget and a captured test surface, and swapping either mid-loop would
mean the report describes a policy the attempts did not run under.

**Deliberately NOT configurable: the weakening refusal.** ``refuse_weakened_tests``
is not a field here and never will be. A self-repair loop that can be satisfied
by deleting an assertion is worse than no loop at all, because it manufactures a
green result; a config key that turns that check off is a foot-gun with a
flattering default, so the check is unconditional in
``alpha.verification.surface.compare_surfaces``.

Wiring status: this module is standalone on purpose. ``AppConfig`` does not
reference it yet, so ``resolve_verification_loop_config()`` reads it
opportunistically off the app config and falls back to these defaults. The
one-line ``AppConfig`` change that makes it a real ``config.yaml`` section is
recorded in ``docs/VERIFICATION_LOOP.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["VerificationLoopConfig", "resolve_verification_loop_config"]


class VerificationLoopConfig(BaseModel):
    """Operator policy for the bounded verification controller."""

    model_config = {"extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description=(
            "When false the controller refuses to run, and every turn it observes reports "
            "UNVERIFIED('verification_loop_disabled') rather than a pass. Default true because an unwired "
            "controller has no effect at all, while a wired controller that silently did nothing would be "
            "indistinguishable from one that verified. The refusal is visible, which is the whole safety of it."
        ),
    )
    max_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Fix attempts per verification. The initial verification run is not an attempt; each repair consumes "
            "one. Exhaustion is a real, reportable outcome (UNVERIFIED with reason budget_exhausted), never an "
            "infinite loop and never a silent give-up."
        ),
    )
    failure_evidence_chars: int = Field(
        default=4000,
        ge=200,
        le=100_000,
        description=(
            "Byte ceiling on the recorded failure output handed to a fix attempt. The text is the recorded tail "
            "verbatim, never a summary or a restatement; when it does not fit, the report carries "
            "failure_evidence_truncated=true so the model is told it is seeing less than the whole failure."
        ),
    )
    test_surface_max_files: int = Field(
        default=500,
        ge=1,
        le=20_000,
        description=(
            "Ceiling on how many test files the before/after surface comparison will read. A surface that cannot "
            "be established in full is reported as unavailable, and an unavailable surface refuses the repair "
            "rather than waving it through."
        ),
    )
    require_repair_prompt_reasoning: bool = Field(
        default=False,
        description=("Reserved, and deliberately inert. The loop's only repair gate is the test-surface comparison, and a knob that sounds like it relaxes that gate is exactly the kind of setting this controller must not have."),
    )


def resolve_verification_loop_config() -> VerificationLoopConfig:
    """Read ``verification_loop`` off the app config when it is wired, else defaults.

    Opportunistic by design: ``AppConfig`` is owned by a different workstream, so
    this module must import and behave correctly both before and after that field
    exists. A malformed or absent section falls back to the defaults rather than
    raising — a config error here must not take a run down, and the controller's
    own guards (budget, surface comparison, fail-closed outcomes) do not depend
    on operator input.
    """
    try:
        from alpha.config.app_config import get_app_config

        section = getattr(get_app_config(), "verification_loop", None)
    except Exception:
        return VerificationLoopConfig()
    if section is None:
        return VerificationLoopConfig()
    if isinstance(section, VerificationLoopConfig):
        return section
    if isinstance(section, dict):
        try:
            return VerificationLoopConfig.model_validate(section)
        except Exception:
            return VerificationLoopConfig()
    return VerificationLoopConfig()
