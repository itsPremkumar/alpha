"""Tests for alpha.graph.failures — graph failure classification."""

from __future__ import annotations

import pytest

from alpha.errors.registry import CodedError
from alpha.graph.failures import GraphFailureClass, classify_graph_failure, raise_graph_failure


class TestClassifyGraphFailure:
    def test_all_failure_classes_have_codes(self) -> None:
        for failure_class in GraphFailureClass:
            definition = classify_graph_failure(failure_class)
            assert definition.code
            assert definition.severity

    def test_cycle_maps_to_dependency_cycle(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.DEPENDENCY_CYCLE)
        assert definition.code == "DEPENDENCY_CYCLE"

    def test_plan_validation_maps_to_plan_validation_failed(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.PLAN_VALIDATION_FAILED)
        assert definition.code == "PLAN_VALIDATION_FAILED"

    def test_verification_maps_to_verification_failed(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.VERIFICATION_FAILED)
        assert definition.code == "VERIFICATION_FAILED"

    def test_approval_required_maps_to_user_approval_required(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.USER_APPROVAL_REQUIRED)
        assert definition.code == "USER_APPROVAL_REQUIRED"

    def test_rejected_maps_to_user_rejected(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.USER_REJECTED)
        assert definition.code == "USER_REJECTED"

    def test_cancelled_maps_to_cancelled(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.CANCELLED)
        assert definition.code == "CANCELLED"

    def test_retry_budget_maps_to_run_quota_exceeded(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.RETRY_BUDGET_EXHAUSTED)
        assert definition.code == "RUN_QUOTA_EXCEEDED"

    def test_capability_maps_to_dependency_unavailable(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.CAPABILITY_UNAVAILABLE)
        assert definition.code == "DEPENDENCY_UNAVAILABLE"

    def test_model_timeout_maps_to_timeout(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.MODEL_TIMEOUT)
        assert definition.code == "TIMEOUT"

    def test_tool_permission_maps_to_sandbox_policy_denied(self) -> None:
        definition = classify_graph_failure(GraphFailureClass.TOOL_PERMISSION_DENIED)
        assert definition.code == "SANDBOX_POLICY_DENIED"


class TestRaiseGraphFailure:
    def test_raises_coded_error(self) -> None:
        with pytest.raises(CodedError) as exc_info:
            raise_graph_failure(GraphFailureClass.DEPENDENCY_CYCLE, "test cycle")
        assert exc_info.value.code == "DEPENDENCY_CYCLE"

    def test_includes_detail(self) -> None:
        with pytest.raises(CodedError) as exc_info:
            raise_graph_failure(GraphFailureClass.PLAN_VALIDATION_FAILED, "bad plan")
        assert "bad plan" in str(exc_info.value)

    def test_includes_context(self) -> None:
        with pytest.raises(CodedError) as exc_info:
            raise_graph_failure(GraphFailureClass.VERIFICATION_FAILED, "failed", context={"task_id": "task-1"})
        assert exc_info.value.context["task_id"] == "task-1"
