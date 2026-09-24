"""Tests for OpenHands Critic & Completion Verification subsystem."""

from alpha.critic.agent_finished import AgentFinishedCritic
from alpha.critic.base import CriticResult, CriticVerdict
from alpha.critic.empty_patch import EmptyPatchCritic
from alpha.critic.pipeline import CriticPipeline
from alpha.critic.rubric import RubricCriterion, RubricEvaluator


def test_critic_result_properties():
    res_app = CriticResult(verdict=CriticVerdict.APPROVED, reason="OK")
    assert res_app.is_approved
    assert not res_app.is_rejected

    res_rej = CriticResult(verdict=CriticVerdict.REJECTED, reason="Failed")
    assert res_rej.is_rejected
    assert not res_app.is_rejected


def test_agent_finished_critic_unresolved_error():
    critic = AgentFinishedCritic(check_unresolved_errors=True)

    # History where last action failed
    bad_history = [
        {"tool_name": "run_command", "status": "success", "exit_code": 0},
        {"tool_name": "run_command", "status": "failed", "exit_code": 1, "error": "SyntaxError in app.py"},
    ]
    res = critic.evaluate("Fix the syntax error", execution_history=bad_history)
    assert res.is_rejected
    assert "SyntaxError" in res.reason
    assert res.diagnostic_prompt is not None

    # History where error was recovered
    good_history = [
        {"tool_name": "run_command", "status": "failed", "exit_code": 1, "error": "SyntaxError in app.py"},
        {"tool_name": "run_command", "status": "success", "exit_code": 0},
    ]
    res_good = critic.evaluate("Fix the syntax error", execution_history=good_history)
    assert res_good.is_approved


def test_empty_patch_critic_read_only_task():
    critic = EmptyPatchCritic()
    # Read-only task shouldn't require a git patch
    res = critic.evaluate("explain how the login authentication works")
    assert res.is_approved
    assert res.metadata.get("is_code_task") is False


def test_empty_patch_critic_history_fallback(tmp_path):
    critic = EmptyPatchCritic()
    # If not a git repo, but history has write operations
    history = [{"tool_name": "write_to_file", "path": "file.py"}]
    res = critic.evaluate("implement the login function", execution_history=history, workspace_dir=str(tmp_path))
    assert res.is_approved


def test_rubric_evaluator(tmp_path):
    # Setup test file
    test_file = tmp_path / "output.txt"
    test_file.write_text("success content")

    evaluator = RubricEvaluator()
    evaluator.add_criterion(RubricCriterion(
        name="output_exists",
        description="Ensure output file exists",
        required_file="output.txt"
    ))
    evaluator.add_criterion(RubricCriterion(
        name="custom_check",
        description="Verify custom condition",
        custom_checker=lambda ctx: True
    ))

    res = evaluator.evaluate("Generate output", workspace_dir=str(tmp_path))
    assert res.is_approved
    assert "output_exists" in res.metadata["passed"]

    # Now add a failing criterion
    evaluator.add_criterion(RubricCriterion(
        name="missing_file",
        description="File that does not exist",
        required_file="missing.txt"
    ))
    res_fail = evaluator.evaluate("Generate output", workspace_dir=str(tmp_path))
    assert res_fail.is_rejected
    assert "missing_file" in res_fail.reason


def test_critic_pipeline():
    pipeline = CriticPipeline(critics=[
        AgentFinishedCritic(min_actions=0),
    ])
    res = pipeline.evaluate(
        task_description="Explain code",
        execution_history=[{"status": "success", "exit_code": 0}],
    )
    assert res.is_approved


def test_rubric_evaluator_abstains_when_no_criteria():
    """Zero criteria means nothing was evaluated: abstain, never APPROVED.

    OLD pinned behavior returned CriticVerdict.APPROVED ("No rubrics defined;
    evaluation passes.") — an approval for an evaluation that never ran.
    """
    res = RubricEvaluator().evaluate("Ship the feature")
    assert not res.is_approved
    assert res.verdict == CriticVerdict.WARNING
    assert res.metadata.get("abstained") is True
    assert "No rubric criteria defined" in res.reason
    assert res.diagnostic_prompt is not None

    # The pipeline must not launder an abstain into an approval either.
    pipeline_res = CriticPipeline(critics=[RubricEvaluator()]).evaluate("Ship the feature")
    assert not pipeline_res.is_approved
    assert pipeline_res.verdict == CriticVerdict.WARNING
