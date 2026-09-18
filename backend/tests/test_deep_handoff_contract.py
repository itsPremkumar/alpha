"""Unit tests for the Deep Handoff Contract and clean-context protocol."""

from agent_workspace.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    DeepTaskSpec,
    build_error_contract,
    build_partial_contract,
    count_words,
    estimate_tokens,
)


class TestDeepTaskSpec:
    def test_valid_spec_round_trip(self):
        spec = DeepTaskSpec(
            goal="Refactor billing module",
            target_files=["a.py", "b.py"],
            token_budget=10000,
            allowed_toolset=["read_file"],
            max_iterations=10,
        )
        restored = DeepTaskSpec.from_dict(spec.to_dict())
        assert restored.goal == spec.goal
        assert restored.target_files == ["a.py", "b.py"]
        assert restored.max_iterations == 10

    def test_empty_goal_rejected(self):
        try:
            DeepTaskSpec(goal="   ")
        except ValueError:
            return
        raise AssertionError("empty goal should raise ValueError")

    def test_non_positive_budget_rejected(self):
        try:
            DeepTaskSpec(goal="goal", token_budget=0)
        except ValueError:
            return
        raise AssertionError("zero token budget should raise ValueError")


class TestDeepHandoffContract:
    def test_summary_capped_at_300_words(self):
        long_summary = " ".join(f"word{i}" for i in range(500))
        contract = DeepHandoffContract(status=DeepExecutionStatus.SUCCESS, executive_summary=long_summary)
        assert count_words(contract.executive_summary) <= 300

    def test_parent_text_within_budget(self):
        contract = DeepHandoffContract(
            status=DeepExecutionStatus.SUCCESS,
            executive_summary="Compact synthesis for parent.",
            unified_diff="x" * 10000,
            invariant_assertions=["invariant one"],
        )
        text = contract.to_parent_text()
        assert len(text) <= 4100
        assert "status=SUCCESS" in text

    def test_serialization_round_trip(self):
        contract = DeepHandoffContract(
            status="SUCCESS",
            executive_summary="All checks passed.",
            session_id="abc123",
            agent_type="architect",
        )
        restored = DeepHandoffContract.from_dict(contract.to_dict())
        assert restored.status == DeepExecutionStatus.SUCCESS
        assert restored.is_success()
        assert restored.session_id == "abc123"

    def test_compression_ratio_positive(self):
        contract = DeepHandoffContract(
            status=DeepExecutionStatus.SUCCESS,
            executive_summary="Synthesis.",
            tokens_consumed=50000,
        )
        assert contract.compression_ratio() > 1.0
        assert estimate_tokens("abcd") >= 1

    def test_partial_and_error_builders(self):
        partial = build_partial_contract("s1", "debugger", "partial work done", tokens_consumed=100)
        assert partial.status == DeepExecutionStatus.PARTIAL_PROGRESS
        error = build_error_contract("s2", "security", "boom")
        assert error.status == DeepExecutionStatus.UNRECOVERABLE_ERROR
        assert "boom" in error.error_detail
