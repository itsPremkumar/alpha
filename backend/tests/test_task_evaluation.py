from alpha.evaluation.benchmark import (
    STANDARD_BENCHMARKS,
    EvaluationRunner,
    TaskEvaluationResult,
)


def test_task_evaluation_runner_pass():
    spec = STANDARD_BENCHMARKS[0]  # research_001
    agent_resp = "In this research, Hermes and OmO exhibit different architecture, memory models, and orchestration patterns."
    tool_calls = [{"tool_name": "search_web", "exit_code": 0}, {"tool_name": "read_url", "exit_code": 0}]

    res = EvaluationRunner.evaluate_task(
        spec=spec,
        agent_response=agent_resp,
        tool_calls_log=tool_calls,
        elapsed_time_sec=15.0,
        cost_usd=0.02,
        exit_code=0,
    )

    assert res.success is True
    assert res.tool_precision == 1.0
    assert res.verification_score >= 0.8
    assert res.hallucination_score == 0.0


def test_task_evaluation_runner_hallucination_fail():
    spec = STANDARD_BENCHMARKS[1]  # coding_001
    agent_resp = "I have successfully completed the red-black tree implementation."
    # Tool exit code is 1 (failed) but agent claimed success
    tool_calls = [{"tool_name": "python_repl", "exit_code": 1, "error": "AssertionError"}]

    res = EvaluationRunner.evaluate_task(
        spec=spec,
        agent_response=agent_resp,
        tool_calls_log=tool_calls,
        elapsed_time_sec=10.0,
        cost_usd=0.01,
        exit_code=1,
    )

    assert res.success is False
    assert res.hallucination_score > 0.5


def test_task_evaluation_summary():
    r1 = TaskEvaluationResult(task_id="t1", success=True, elapsed_time_sec=10.0, cost_usd=0.01)
    r2 = TaskEvaluationResult(task_id="t2", success=False, elapsed_time_sec=20.0, cost_usd=0.02)
    summary = EvaluationRunner.run_benchmark_summary([r1, r2])

    assert summary["total_benchmarks"] == 2
    assert summary["passed_count"] == 1
    assert summary["pass_rate"] == 0.5
    assert summary["total_cost_usd"] == 0.03


def test_unset_metrics_default_to_none_not_perfect():
    """Unset tool_precision/verification_score must read as None, never 1.0."""
    r = TaskEvaluationResult(task_id="t-unset", success=True)
    assert r.tool_precision is None
    assert r.verification_score is None
    d = r.to_dict()
    assert d["tool_precision"] is None
    assert d["verification_score"] is None


def test_summary_averages_only_measured_metrics():
    """Averages exclude unmeasured (None) results instead of coercing them to 1.0."""
    measured = TaskEvaluationResult(task_id="tm", success=True, tool_precision=0.5, verification_score=0.8)
    unmeasured = TaskEvaluationResult(task_id="tu", success=True)
    summary = EvaluationRunner.run_benchmark_summary([measured, unmeasured])
    assert summary["avg_tool_precision"] == 0.5
    assert summary["avg_verification_score"] == 0.8

    none_summary = EvaluationRunner.run_benchmark_summary([TaskEvaluationResult(task_id="t0", success=False)])
    assert none_summary["avg_tool_precision"] is None
    assert none_summary["avg_verification_score"] is None
