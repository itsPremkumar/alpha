"""Deterministic reasoning policy from explicit, server-derived task signals.

The policy is a pure classifier.  It does not inspect free-form prompts, call a
model, select a provider, authorize a tool, or claim that a task is complete.
Host code must derive the signals from server-owned context; model-authored
prose is not a policy input.

An under-determined task resolves to the documented ``STANDARD`` default with
``under_determined=True`` and a reason.  A boundary value always maps the same
way, and every emitted limit is only a proposal that the existing runtime and
budget owners may further restrict.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from alpha.reasoning.models import (
    ClampedModel,
    ReasoningMode,
    ReversibilityLevel,
    RiskLevel,
)

__all__ = [
    "BudgetSignals",
    "ModelCapabilities",
    "PolicyThresholds",
    "ReasoningEffort",
    "ReasoningPolicy",
    "TaskClass",
    "TaskClassification",
    "TaskSignals",
    "VerifierKind",
    "classify_task",
    "select_policy",
]


class TaskClass(StrEnum):
    TRIVIAL = "trivial"
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"
    RESEARCH = "research"
    CODE = "code"
    HIGH_RISK = "high_risk"
    SYSTEM_CHANGE = "system_change"
    SELF_IMPROVEMENT = "self_improvement"


class ReasoningEffort(StrEnum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAXIMAL = "maximal"


class VerifierKind(StrEnum):
    NONE = "none"
    LIGHTWEIGHT = "lightweight"
    EVIDENCE = "evidence"
    TEST = "test"
    COMPOSITE = "composite"
    INDEPENDENT = "independent"
    BENCHMARK = "benchmark"
    HUMAN_APPROVAL = "human_approval"


_RISK_SCORE = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class ModelCapabilities(ClampedModel):
    """Provider capabilities supplied by the existing model layer."""

    provider_name: str = Field(default="unknown", min_length=1, max_length=128)
    native_reasoning: bool = False
    supports_reasoning_effort: bool = False
    supports_reasoning_summaries: bool = False
    supports_interleaved_thinking: bool = False
    supports_structured_output: bool = False
    supports_parallel_tool_calls: bool = False


class BudgetSignals(ClampedModel):
    """Budget headroom advertised by the existing runtime owner."""

    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    max_wall_clock_seconds: float | None = None
    max_subagents: int | None = None
    max_branches: int | None = None

    @model_validator(mode="after")
    def _clamp_values(self) -> Self:
        for name in ("max_model_calls", "max_tool_calls", "max_tokens", "max_subagents", "max_branches"):
            if getattr(self, name) is not None:
                self.clamp_field(name, 0, 1_000_000, integer=True)
        if self.max_wall_clock_seconds is not None:
            self.clamp_field("max_wall_clock_seconds", 0.0, 604_800.0)
        return self


class PolicyThresholds(ClampedModel):
    trivial_max_complexity: float = Field(default=0.15)
    fast_max_complexity: float = Field(default=0.35)
    deep_min_complexity: float = Field(default=0.75)
    deep_min_ambiguity: float = Field(default=0.80)
    deep_tool_count: int = Field(default=8)
    high_risk_min_score: int = Field(default=2)
    historical_low_success: float = Field(default=0.35)
    weak_success_iteration_factor: float = Field(default=0.75)
    short_budget_seconds: float = Field(default=120.0)
    short_budget_max_iterations: int = Field(default=4)
    tiny_token_budget: int = Field(default=4000)

    @model_validator(mode="after")
    def _clamp_and_order(self) -> Self:
        for name in (
            "trivial_max_complexity",
            "fast_max_complexity",
            "deep_min_complexity",
            "deep_min_ambiguity",
            "historical_low_success",
            "weak_success_iteration_factor",
            "short_budget_seconds",
        ):
            upper = 604_800.0 if name == "short_budget_seconds" else 1.0
            self.clamp_field(name, 0.0, upper)
        self.clamp_field("deep_tool_count", 1, 10_000, integer=True)
        self.clamp_field("high_risk_min_score", 0, 3, integer=True)
        self.clamp_field("short_budget_max_iterations", 1, 1000, integer=True)
        self.clamp_field("tiny_token_budget", 0, 10_000_000, integer=True)
        if not self.trivial_max_complexity < self.fast_max_complexity < self.deep_min_complexity:
            raise ValueError("complexity thresholds must satisfy trivial < fast < deep")
        return self


class TaskSignals(ClampedModel):
    """Explicit signals; ``None`` means the host did not establish the fact."""

    complexity: float | None = None
    risk: RiskLevel | None = None
    reversibility: ReversibilityLevel | None = None
    ambiguity: float | None = None
    expected_tool_count: int | None = None
    needs_evidence: bool | None = None
    needs_code: bool | None = None
    needs_branching: bool | None = None
    needs_multi_agent: bool | None = None
    system_change: bool | None = None
    self_improvement: bool | None = None
    interactive: bool = True
    model_capabilities: ModelCapabilities | None = None
    budgets: BudgetSignals | None = None
    historical_success: float | None = None

    @model_validator(mode="after")
    def _clamp_values(self) -> Self:
        for name in ("complexity", "ambiguity", "historical_success"):
            if getattr(self, name) is not None:
                self.clamp_field(name, 0.0, 1.0)
        if self.expected_tool_count is not None:
            self.clamp_field("expected_tool_count", 0, 10_000, integer=True)
        return self


class TaskClassification(ClampedModel):
    task_class: TaskClass
    under_determined: bool
    reason: str
    matched_signals: tuple[str, ...] = ()


class ReasoningPolicy(ClampedModel):
    task_class: TaskClass
    mode: ReasoningMode
    effort: ReasoningEffort
    max_iterations: int = Field(ge=0)
    max_branches: int = Field(ge=0)
    verifier: VerifierKind
    reflection: bool
    subagents: bool
    evidence_required: bool
    native_thinking: bool
    approval_required: bool
    independent_verification: bool
    rollback_required: bool
    provider_reasoning_summaries: bool
    interleaved_thinking: bool
    structured_output: bool
    parallel_tool_calls: bool
    under_determined: bool
    reason: str
    matched_signals: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _clamp_limits(self) -> Self:
        self.clamp_field("max_iterations", 0, 1000, integer=True)
        self.clamp_field("max_branches", 0, 1000, integer=True)
        return self


class _PolicyTemplate(ClampedModel):
    effort: ReasoningEffort
    max_iterations: int
    max_branches: int
    verifier: VerifierKind
    reflection: bool
    subagents: bool
    evidence_required: bool
    wants_native_reasoning: bool
    approval_required: bool
    independent_verification: bool
    rollback_required: bool


_POLICY_TEMPLATES: dict[TaskClass, _PolicyTemplate] = {
    TaskClass.TRIVIAL: _PolicyTemplate(
        effort=ReasoningEffort.MINIMAL,
        max_iterations=1,
        max_branches=0,
        verifier=VerifierKind.NONE,
        reflection=False,
        subagents=False,
        evidence_required=False,
        wants_native_reasoning=False,
        approval_required=False,
        independent_verification=False,
        rollback_required=False,
    ),
    TaskClass.FAST: _PolicyTemplate(
        effort=ReasoningEffort.LOW,
        max_iterations=3,
        max_branches=0,
        verifier=VerifierKind.LIGHTWEIGHT,
        reflection=False,
        subagents=False,
        evidence_required=False,
        wants_native_reasoning=False,
        approval_required=False,
        independent_verification=False,
        rollback_required=False,
    ),
    TaskClass.STANDARD: _PolicyTemplate(
        effort=ReasoningEffort.MEDIUM,
        max_iterations=6,
        max_branches=0,
        verifier=VerifierKind.EVIDENCE,
        reflection=False,
        subagents=False,
        evidence_required=False,
        wants_native_reasoning=True,
        approval_required=False,
        independent_verification=False,
        rollback_required=False,
    ),
    TaskClass.DEEP: _PolicyTemplate(
        effort=ReasoningEffort.HIGH,
        max_iterations=12,
        max_branches=2,
        verifier=VerifierKind.COMPOSITE,
        reflection=True,
        subagents=True,
        evidence_required=True,
        wants_native_reasoning=True,
        approval_required=False,
        independent_verification=False,
        rollback_required=False,
    ),
    TaskClass.RESEARCH: _PolicyTemplate(
        effort=ReasoningEffort.HIGH,
        max_iterations=16,
        max_branches=2,
        verifier=VerifierKind.EVIDENCE,
        reflection=True,
        subagents=True,
        evidence_required=True,
        wants_native_reasoning=True,
        approval_required=False,
        independent_verification=False,
        rollback_required=False,
    ),
    TaskClass.CODE: _PolicyTemplate(
        effort=ReasoningEffort.HIGH,
        max_iterations=16,
        max_branches=2,
        verifier=VerifierKind.TEST,
        reflection=True,
        subagents=True,
        evidence_required=True,
        wants_native_reasoning=True,
        approval_required=False,
        independent_verification=False,
        rollback_required=False,
    ),
    TaskClass.HIGH_RISK: _PolicyTemplate(
        effort=ReasoningEffort.MAXIMAL,
        max_iterations=8,
        max_branches=0,
        verifier=VerifierKind.INDEPENDENT,
        reflection=True,
        subagents=True,
        evidence_required=True,
        wants_native_reasoning=True,
        approval_required=True,
        independent_verification=True,
        rollback_required=False,
    ),
    TaskClass.SYSTEM_CHANGE: _PolicyTemplate(
        effort=ReasoningEffort.MAXIMAL,
        max_iterations=8,
        max_branches=0,
        verifier=VerifierKind.HUMAN_APPROVAL,
        reflection=True,
        subagents=True,
        evidence_required=True,
        wants_native_reasoning=True,
        approval_required=True,
        independent_verification=True,
        rollback_required=True,
    ),
    TaskClass.SELF_IMPROVEMENT: _PolicyTemplate(
        effort=ReasoningEffort.HIGH,
        max_iterations=24,
        max_branches=2,
        verifier=VerifierKind.BENCHMARK,
        reflection=True,
        subagents=True,
        evidence_required=True,
        wants_native_reasoning=True,
        approval_required=True,
        independent_verification=True,
        rollback_required=True,
    ),
}


def classify_task(
    signals: TaskSignals,
    thresholds: PolicyThresholds = PolicyThresholds(),
) -> TaskClassification:
    """Classify from explicit signals with a stable precedence order."""

    matched: list[str] = []
    if signals.self_improvement is True:
        matched.append("self_improvement")
        return TaskClassification(
            task_class=TaskClass.SELF_IMPROVEMENT,
            under_determined=False,
            reason="self-improvement signal requires benchmark, approval, and rollback gates",
            matched_signals=tuple(matched),
        )
    if signals.system_change is True:
        matched.append("system_change")
        return TaskClassification(
            task_class=TaskClass.SYSTEM_CHANGE,
            under_determined=False,
            reason="system-change signal requires human approval, independent verification, and rollback",
            matched_signals=tuple(matched),
        )

    risk_score = _RISK_SCORE[signals.risk] if signals.risk is not None else None
    if risk_score is not None and risk_score >= thresholds.high_risk_min_score:
        matched.append("risk")
    if signals.reversibility is ReversibilityLevel.IRREVERSIBLE:
        matched.append("reversibility")
    if matched:
        if signals.reversibility is not None:
            matched.append("reversibility")
        return TaskClassification(
            task_class=TaskClass.HIGH_RISK,
            under_determined=False,
            reason="high risk or irreversible work requires independent verification and approval",
            matched_signals=tuple(dict.fromkeys(matched)),
        )

    if signals.needs_code is True:
        matched.append("needs_code")
        return TaskClassification(
            task_class=TaskClass.CODE,
            under_determined=False,
            reason="code work is test/compiler verified and reflection enabled",
            matched_signals=tuple(matched),
        )
    if signals.needs_evidence is True:
        matched.append("needs_evidence")
        return TaskClassification(
            task_class=TaskClass.RESEARCH,
            under_determined=False,
            reason="external evidence is required, so research evidence verification is selected",
            matched_signals=tuple(matched),
        )

    deep_reasons: list[str] = []
    if signals.complexity is not None:
        matched.append("complexity")
        if signals.complexity >= thresholds.deep_min_complexity:
            deep_reasons.append("complexity")
    if signals.ambiguity is not None:
        matched.append("ambiguity")
        if signals.ambiguity >= thresholds.deep_min_ambiguity:
            deep_reasons.append("ambiguity")
    if signals.expected_tool_count is not None:
        matched.append("expected_tool_count")
        if signals.expected_tool_count >= thresholds.deep_tool_count:
            deep_reasons.append("expected_tool_count")
    if signals.needs_branching is True:
        matched.append("needs_branching")
        deep_reasons.append("needs_branching")
    if signals.needs_multi_agent is True:
        matched.append("needs_multi_agent")
        deep_reasons.append("needs_multi_agent")
    if deep_reasons:
        return TaskClassification(
            task_class=TaskClass.DEEP,
            under_determined=False,
            reason="deep signals: " + ", ".join(deep_reasons),
            matched_signals=tuple(dict.fromkeys(matched)),
        )

    if signals.complexity is not None and signals.complexity <= thresholds.trivial_max_complexity:
        if not signals.expected_tool_count and signals.needs_code is not True and signals.needs_evidence is not True:
            matched.append("trivial_complexity")
            return TaskClassification(
                task_class=TaskClass.TRIVIAL,
                under_determined=False,
                reason="low complexity and no expected tools/evidence/code",
                matched_signals=tuple(dict.fromkeys(matched)),
            )
    if signals.complexity is not None and signals.complexity <= thresholds.fast_max_complexity:
        if not signals.expected_tool_count and signals.needs_code is not True and signals.needs_evidence is not True:
            matched.append("fast_complexity")
            return TaskClassification(
                task_class=TaskClass.FAST,
                under_determined=False,
                reason="bounded complexity and no expected tools/evidence/code",
                matched_signals=tuple(dict.fromkeys(matched)),
            )

    discriminating = any(
        value is not None
        for value in (
            signals.complexity,
            signals.risk,
            signals.reversibility,
            signals.ambiguity,
            signals.expected_tool_count,
            signals.needs_evidence,
            signals.needs_code,
            signals.needs_branching,
            signals.needs_multi_agent,
            signals.system_change,
            signals.self_improvement,
        )
    )
    if not discriminating:
        return TaskClassification(
            task_class=TaskClass.STANDARD,
            under_determined=True,
            reason="under-determined: no discriminating task signal was supplied; disclosed STANDARD default",
            matched_signals=(),
        )
    return TaskClassification(
        task_class=TaskClass.STANDARD,
        under_determined=False,
        reason="explicit signals do not meet a more specific class boundary",
        matched_signals=tuple(dict.fromkeys(matched)),
    )


_EFFORT_ORDER = (
    ReasoningEffort.MINIMAL,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
    ReasoningEffort.MAXIMAL,
)


def _lower_effort(effort: ReasoningEffort) -> ReasoningEffort:
    index = _EFFORT_ORDER.index(effort)
    return _EFFORT_ORDER[max(0, index - 1)]


def select_policy(
    signals: TaskSignals,
    *,
    thresholds: PolicyThresholds = PolicyThresholds(),
    default_mode: ReasoningMode = ReasoningMode.ROUTED,
) -> ReasoningPolicy:
    """Emit the documented strategy for a classified task.

    The result is a proposal.  Existing model, tool, approval, budget, and
    verification owners remain authoritative and may only narrow it.
    """

    classification = classify_task(signals, thresholds)
    template = _POLICY_TEMPLATES[classification.task_class]
    capabilities = signals.model_capabilities or ModelCapabilities()
    budgets = signals.budgets or BudgetSignals()
    reasons = [classification.reason]
    reasons.append("interactive clarification remains available to the existing runtime owner" if signals.interactive else "non-interactive run: clarification is unavailable to the reasoning policy")
    if capabilities.provider_name != "unknown":
        reasons.append(f"provider capability record supplied by {capabilities.provider_name}")

    effort = template.effort
    max_iterations = template.max_iterations
    max_branches = template.max_branches
    subagents = template.subagents
    reflection = template.reflection

    if signals.historical_success is not None and signals.historical_success < thresholds.historical_low_success:
        effort = _lower_effort(effort)
        max_iterations = max(1, int(max_iterations * thresholds.weak_success_iteration_factor))
        subagents = False
        reasons.append("historical success is below the disclosed threshold; effort/delegation reduced")

    if budgets.max_model_calls is not None:
        max_iterations = min(max_iterations, budgets.max_model_calls)
        reasons.append(f"model-call headroom caps proposed iterations at {max_iterations}")
    if budgets.max_wall_clock_seconds is not None and budgets.max_wall_clock_seconds < thresholds.short_budget_seconds:
        effort = _lower_effort(effort)
        max_iterations = min(max_iterations, thresholds.short_budget_max_iterations)
        reasons.append("short wall-clock headroom reduces effort and iterations")
    if budgets.max_tokens is not None and budgets.max_tokens < thresholds.tiny_token_budget:
        effort = _lower_effort(effort)
        reasons.append("token headroom is below the disclosed native-reasoning floor")
    if budgets.max_tool_calls is not None and budgets.max_tool_calls == 0:
        subagents = False
        reflection = False
        reasons.append("no tool-call headroom disables delegated/reflection work")
    if budgets.max_subagents is not None:
        if budgets.max_subagents == 0:
            subagents = False
            reasons.append("subagent headroom disables delegated work")
    if budgets.max_branches is not None:
        max_branches = min(max_branches, budgets.max_branches)
        reasons.append("branch headroom applied")
    if budgets.max_tokens is not None and max_iterations * 1000 > budgets.max_tokens:
        max_iterations = max(0, budgets.max_tokens // 1000)
        reasons.append("token headroom further reduces proposed iterations")

    native_requested = template.wants_native_reasoning and capabilities.native_reasoning
    if classification.under_determined:
        mode = default_mode
        if mode in {ReasoningMode.NATIVE, ReasoningMode.HYBRID} and not capabilities.native_reasoning:
            mode = ReasoningMode.EXPLICIT
            reasons.append("configured native mode downgraded because the model does not advertise native reasoning")
    elif classification.task_class in {TaskClass.TRIVIAL, TaskClass.FAST} and default_mode is ReasoningMode.ROUTED:
        mode = ReasoningMode.ROUTED
    elif native_requested:
        mode = ReasoningMode.HYBRID
    else:
        mode = ReasoningMode.EXPLICIT
        if template.wants_native_reasoning and not capabilities.native_reasoning:
            reasons.append("native reasoning unavailable; explicit Alpha reasoning selected")
    if (
        capabilities.native_reasoning
        and not capabilities.supports_reasoning_effort
        and effort
        in {
            ReasoningEffort.HIGH,
            ReasoningEffort.MAXIMAL,
        }
    ):
        effort = ReasoningEffort.MEDIUM
        reasons.append("provider does not expose reasoning-effort control; effort capped at medium")

    return ReasoningPolicy(
        task_class=classification.task_class,
        mode=mode,
        effort=effort,
        max_iterations=max_iterations,
        max_branches=max_branches,
        verifier=template.verifier,
        reflection=reflection,
        subagents=subagents,
        evidence_required=template.evidence_required,
        native_thinking=native_requested and capabilities.native_reasoning,
        approval_required=template.approval_required,
        independent_verification=template.independent_verification,
        rollback_required=template.rollback_required,
        provider_reasoning_summaries=capabilities.native_reasoning and capabilities.supports_reasoning_summaries,
        interleaved_thinking=capabilities.native_reasoning and capabilities.supports_interleaved_thinking,
        structured_output=capabilities.supports_structured_output,
        parallel_tool_calls=capabilities.supports_parallel_tool_calls,
        under_determined=classification.under_determined,
        reason="; ".join(dict.fromkeys(reasons)),
        matched_signals=classification.matched_signals,
    )
