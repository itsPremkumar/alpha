from __future__ import annotations

import pytest

from alpha.reasoning.models import ReasoningMode, ReversibilityLevel, RiskLevel
from alpha.reasoning.policy import (
    BudgetSignals,
    ModelCapabilities,
    ReasoningEffort,
    TaskClass,
    TaskSignals,
    VerifierKind,
    classify_task,
    select_policy,
)


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (TaskSignals(complexity=0.1, expected_tool_count=0), TaskClass.TRIVIAL),
        (TaskSignals(complexity=0.3, expected_tool_count=0), TaskClass.FAST),
        (TaskSignals(complexity=0.5, expected_tool_count=2), TaskClass.STANDARD),
        (TaskSignals(complexity=0.8), TaskClass.DEEP),
        (TaskSignals(needs_evidence=True), TaskClass.RESEARCH),
        (TaskSignals(needs_code=True), TaskClass.CODE),
        (TaskSignals(risk=RiskLevel.HIGH, reversibility=ReversibilityLevel.REVERSIBLE), TaskClass.HIGH_RISK),
        (TaskSignals(reversibility=ReversibilityLevel.IRREVERSIBLE), TaskClass.HIGH_RISK),
        (TaskSignals(system_change=True), TaskClass.SYSTEM_CHANGE),
        (TaskSignals(self_improvement=True), TaskClass.SELF_IMPROVEMENT),
    ],
)
def test_each_documented_class_maps_to_its_documented_strategy(signals: TaskSignals, expected: TaskClass) -> None:
    classification = classify_task(signals)
    assert classification.task_class is expected
    assert classification.under_determined is False

    policy = select_policy(signals)
    assert policy.task_class is expected
    if expected is TaskClass.TRIVIAL:
        assert policy.mode is ReasoningMode.ROUTED
        assert policy.effort is ReasoningEffort.MINIMAL
        assert policy.verifier is VerifierKind.NONE
    elif expected is TaskClass.FAST:
        assert policy.mode is ReasoningMode.ROUTED
        assert policy.verifier is VerifierKind.LIGHTWEIGHT
    elif expected is TaskClass.STANDARD:
        assert policy.mode is ReasoningMode.EXPLICIT
        assert policy.verifier is VerifierKind.EVIDENCE
    elif expected is TaskClass.DEEP:
        assert policy.mode is ReasoningMode.EXPLICIT
        assert policy.reflection is True
        assert policy.subagents is True
        assert policy.evidence_required is True
    elif expected is TaskClass.RESEARCH:
        assert policy.verifier is VerifierKind.EVIDENCE
        assert policy.max_branches == 2
    elif expected is TaskClass.CODE:
        assert policy.verifier is VerifierKind.TEST
        assert policy.evidence_required is True
    elif expected is TaskClass.HIGH_RISK:
        assert policy.approval_required is True
        assert policy.independent_verification is True
        assert policy.verifier is VerifierKind.INDEPENDENT
    elif expected is TaskClass.SYSTEM_CHANGE:
        assert policy.verifier is VerifierKind.HUMAN_APPROVAL
        assert policy.rollback_required is True
    elif expected is TaskClass.SELF_IMPROVEMENT:
        assert policy.verifier is VerifierKind.BENCHMARK
        assert policy.rollback_required is True


def test_boundaries_are_inclusive_and_deterministic() -> None:
    assert classify_task(TaskSignals(complexity=0.15, expected_tool_count=0)).task_class is TaskClass.TRIVIAL
    assert classify_task(TaskSignals(complexity=0.35, expected_tool_count=0)).task_class is TaskClass.FAST
    assert classify_task(TaskSignals(complexity=0.75)).task_class is TaskClass.DEEP
    assert classify_task(TaskSignals(expected_tool_count=8)).task_class is TaskClass.DEEP
    assert classify_task(TaskSignals(ambiguity=0.8)).task_class is TaskClass.DEEP
    assert classify_task(TaskSignals(needs_branching=True)).task_class is TaskClass.DEEP
    assert classify_task(TaskSignals(needs_multi_agent=True)).task_class is TaskClass.DEEP
    first = classify_task(TaskSignals(complexity=0.5, expected_tool_count=2))
    second = classify_task(TaskSignals(complexity=0.5, expected_tool_count=2))
    assert first == second


def test_under_determination_uses_a_disclosed_default_not_a_guess() -> None:
    classification = classify_task(TaskSignals())
    policy = select_policy(TaskSignals())
    assert classification.task_class is TaskClass.STANDARD
    assert classification.under_determined is True
    assert "under-determined" in classification.reason
    assert policy.under_determined is True
    assert policy.mode is ReasoningMode.ROUTED
    assert "under-determined" in policy.reason


def test_native_capability_is_used_only_when_advertised() -> None:
    explicit = select_policy(TaskSignals(needs_code=True, model_capabilities=ModelCapabilities(provider_name="local")))
    assert explicit.mode is ReasoningMode.EXPLICIT
    assert explicit.native_thinking is False
    assert "local" in explicit.reason
    assert "native reasoning unavailable" in explicit.reason

    native = select_policy(
        TaskSignals(
            needs_code=True,
            model_capabilities=ModelCapabilities(
                provider_name="reasoning-provider",
                native_reasoning=True,
                supports_reasoning_effort=True,
                supports_reasoning_summaries=True,
                supports_interleaved_thinking=True,
                supports_structured_output=True,
                supports_parallel_tool_calls=True,
            ),
        )
    )
    assert native.mode is ReasoningMode.HYBRID
    assert native.native_thinking is True
    assert native.provider_reasoning_summaries is True
    assert native.interleaved_thinking is True
    assert native.structured_output is True
    assert native.parallel_tool_calls is True


def test_budget_and_history_only_narrow_the_policy() -> None:
    base = select_policy(TaskSignals(needs_evidence=True))
    narrowed = select_policy(
        TaskSignals(
            needs_evidence=True,
            historical_success=0.1,
            budgets=BudgetSignals(
                max_model_calls=3,
                max_tool_calls=0,
                max_tokens=1000,
                max_wall_clock_seconds=10,
                max_subagents=0,
                max_branches=0,
            ),
        )
    )
    assert base.max_iterations == 16
    assert narrowed.max_iterations == 1
    assert narrowed.max_branches == 0
    assert narrowed.subagents is False
    assert narrowed.reflection is False
    assert narrowed.effort in {ReasoningEffort.MINIMAL, ReasoningEffort.LOW, ReasoningEffort.MEDIUM}
    assert "historical success" in narrowed.reason
    assert "token headroom" in narrowed.reason
    assert "non-interactive" in select_policy(TaskSignals(needs_code=True, interactive=False)).reason


def test_out_of_range_signals_are_clamped_and_disclosed() -> None:
    signals = TaskSignals(complexity=1.4, ambiguity=-0.2, expected_tool_count=-3, historical_success=2.0)
    assert signals.complexity == 1.0
    assert signals.ambiguity == 0.0
    assert signals.expected_tool_count == 0
    assert signals.historical_success == 1.0
    assert len(signals.clamp_disclosures) == 4
    assert classify_task(signals).task_class is TaskClass.DEEP
