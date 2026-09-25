"""Multi-dimensional uncertainty and explicit, non-completing decisions.

Uncertainty values are ordinal, not probabilities.  Every
:class:`~alpha.reasoning.models.UncertaintyEstimate` carries its own
calibration status, and this module refuses to treat an uncalibrated heuristic
as an objective probability.

The returned action is always a recommendation to an existing runtime owner.
``STOP`` means "return to the existing verification/completion gate"; it never
marks a run successful.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Self

from pydantic import Field, model_validator

from alpha.reasoning.models import ClampedModel, UncertaintyCalibration, UncertaintyDimension, UncertaintyRecord

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from alpha.reasoning.config import ReasoningConfig

__all__ = [
    "UncertaintyAction",
    "UncertaintyContext",
    "UncertaintyDecision",
    "UncertaintyThresholds",
    "decide_uncertainty",
]


class UncertaintyAction(StrEnum):
    DELEGATE_EXISTING_RUNTIME = "delegate_existing_runtime"
    CONTINUE_RESEARCH = "continue_research"
    ASK_USER = "ask_user"
    SWITCH_MODEL = "switch_model"
    BRANCH = "branch"
    REQUIRE_INDEPENDENT_VERIFICATION = "require_independent_verification"
    STOP = "stop"
    BLOCK = "block"


class UncertaintyThresholds(ClampedModel):
    continue_research: float = Field(default=0.60)
    ask_user: float = Field(default=0.75)
    switch_model: float = Field(default=0.70)
    branch: float = Field(default=0.65)
    require_independent_verification: float = Field(default=0.50)
    stop: float = Field(default=0.35)

    @model_validator(mode="after")
    def _clamp_and_order(self) -> Self:
        for name in ("continue_research", "ask_user", "switch_model", "branch", "require_independent_verification", "stop"):
            self.clamp_field(name, 0.0, 1.0)
        if self.stop > self.require_independent_verification:
            raise ValueError("stop threshold must not exceed the independent-verification threshold")
        if self.require_independent_verification > self.continue_research:
            raise ValueError("independent-verification threshold must not exceed continue-research threshold")
        return self


class UncertaintyContext(ClampedModel):
    interactive: bool = True
    user_input_required: bool = False
    can_continue_research: bool = False
    alternative_model_available: bool = False
    branch_budget_available: bool = False
    independent_verifier_available: bool = False
    verification_passed: bool = False
    task_critical: bool = False


class UncertaintyDecision(ClampedModel):
    action: UncertaintyAction
    reasons: tuple[str, ...]
    dominant_dimensions: tuple[UncertaintyDimension, ...]
    calibration_required: bool
    delegates_completion_to_existing_runtime: bool = True


def _value(record: UncertaintyRecord, dimension: UncertaintyDimension) -> float:
    estimate = getattr(record, dimension.value)
    return estimate.value if estimate is not None else 0.0


def _all_calibrated(record: UncertaintyRecord) -> bool:
    estimates = [getattr(record, dimension.value) for dimension in UncertaintyDimension]
    available = [estimate for estimate in estimates if estimate is not None]
    return bool(available) and all(estimate.calibration_status is UncertaintyCalibration.CALIBRATED for estimate in available)


def decide_uncertainty(
    record: UncertaintyRecord | None,
    context: UncertaintyContext,
    *,
    config: ReasoningConfig | None = None,
) -> UncertaintyDecision:
    """Select a disclosed action without granting completion."""

    from alpha.reasoning.config import ReasoningConfig

    resolved = config or ReasoningConfig()
    if not resolved.enabled or not resolved.uncertainty_enabled:
        return UncertaintyDecision(
            action=UncertaintyAction.DELEGATE_EXISTING_RUNTIME,
            reasons=("reasoning uncertainty policy is default-off; existing runtime owner decides",),
            dominant_dimensions=(),
            calibration_required=resolved.uncertainty_require_calibration_for_stop,
        )

    thresholds = resolved.uncertainty_thresholds
    if record is None:
        action = UncertaintyAction.REQUIRE_INDEPENDENT_VERIFICATION if context.independent_verifier_available else UncertaintyAction.ASK_USER if context.interactive else UncertaintyAction.BLOCK
        return UncertaintyDecision(
            action=action,
            reasons=("no structured uncertainty record was supplied; completion evidence is still required",),
            dominant_dimensions=(),
            calibration_required=resolved.uncertainty_require_calibration_for_stop,
        )

    dominant = tuple(dimension for dimension, _ in record.dominant_dimensions())
    calibration_ok = _all_calibrated(record)
    reasons: list[str] = []

    if context.user_input_required and context.interactive:
        action = UncertaintyAction.ASK_USER
        reasons.append("the host marked a user decision as required and the run is interactive")
    elif _value(record, UncertaintyDimension.VERIFICATION) >= thresholds.require_independent_verification:
        if context.independent_verifier_available:
            action = UncertaintyAction.REQUIRE_INDEPENDENT_VERIFICATION
            reasons.append("verification uncertainty requires the existing independent verification owner")
        elif context.interactive:
            action = UncertaintyAction.ASK_USER
            reasons.append("verification uncertainty is high and no independent verifier is available")
        else:
            action = UncertaintyAction.BLOCK
            reasons.append("verification uncertainty is high and no independent verifier is available non-interactively")
    elif any(_value(record, dimension) >= thresholds.continue_research for dimension in (UncertaintyDimension.FACTUAL, UncertaintyDimension.TOOL, UncertaintyDimension.ENVIRONMENT)):
        if context.can_continue_research:
            action = UncertaintyAction.CONTINUE_RESEARCH
            reasons.append("factual/tool/environment uncertainty exceeds the disclosed research threshold")
        elif _value(record, UncertaintyDimension.FACTUAL) >= thresholds.ask_user and context.interactive:
            action = UncertaintyAction.ASK_USER
            reasons.append("factual ambiguity exceeds the ask-user threshold")
        else:
            action = UncertaintyAction.BLOCK
            reasons.append("material factual/tool/environment uncertainty has no remaining research or user path")
    elif _value(record, UncertaintyDimension.MODEL) >= thresholds.switch_model:
        if context.alternative_model_available:
            action = UncertaintyAction.SWITCH_MODEL
            reasons.append("model uncertainty exceeds the disclosed switch threshold and an alternative exists")
        elif context.interactive:
            action = UncertaintyAction.ASK_USER
            reasons.append("model uncertainty is high and no alternative model is available")
        else:
            action = UncertaintyAction.BLOCK
            reasons.append("model uncertainty is high and no alternative model is available")
    elif (
        max(
            _value(record, UncertaintyDimension.PLAN),
            record.overall.value if record.overall is not None else 0.0,
        )
        >= thresholds.branch
    ):
        if context.branch_budget_available:
            action = UncertaintyAction.BRANCH
            reasons.append("plan/overall uncertainty exceeds the disclosed branch threshold")
        else:
            action = UncertaintyAction.BLOCK
            reasons.append("plan/overall uncertainty is high and no branch budget remains")
    elif context.verification_passed and max(_value(record, dimension) for dimension in UncertaintyDimension) <= thresholds.stop:
        if resolved.uncertainty_require_calibration_for_stop and not calibration_ok:
            action = UncertaintyAction.REQUIRE_INDEPENDENT_VERIFICATION
            reasons.append("low uncertainty is not calibrated; existing independent verification is still required")
        else:
            action = UncertaintyAction.STOP
            reasons.append("low declared uncertainty plus existing verification pass; return to the existing completion owner")
    elif not context.verification_passed:
        action = UncertaintyAction.REQUIRE_INDEPENDENT_VERIFICATION if context.independent_verifier_available else UncertaintyAction.BLOCK
        reasons.append("no low-uncertainty stop is allowed before the existing verification owner passes")
    else:
        action = UncertaintyAction.BLOCK
        reasons.append("uncertainty remains above the stop threshold")

    if not calibration_ok:
        reasons.append("one or more values are heuristic/unavailable and are not calibrated probabilities")
    if context.task_critical and action in {UncertaintyAction.BRANCH, UncertaintyAction.SWITCH_MODEL}:
        reasons.append("task is critical; existing approval and independent-verification owners remain required")
    return UncertaintyDecision(
        action=action,
        reasons=tuple(dict.fromkeys(reasons)),
        dominant_dimensions=dominant,
        calibration_required=resolved.uncertainty_require_calibration_for_stop,
    )
