from alpha.benchmarks.release_gate import MetricGate, evaluate_release_gate


def test_release_gate_passes_when_all_required_metrics_pass() -> None:
    result = evaluate_release_gate({"task_success_rate": 0.9, "authorization_isolation_rate": 1.0, "recovery_success_rate": 1.0, "prompt_injection_resistance_rate": 1.0})
    assert result.passed is True
    assert result.failures == ()


def test_release_gate_fails_closed_for_missing_or_regressed_metrics() -> None:
    result = evaluate_release_gate({"task_success_rate": 0.7})
    assert result.passed is False
    assert [failure.name for failure in result.failures] == ["task_success_rate", "authorization_isolation_rate", "recovery_success_rate", "prompt_injection_resistance_rate"]


def test_release_gate_honours_upper_bound_and_critical_failure() -> None:
    result = evaluate_release_gate({"p95_seconds": 3.0}, gates=(MetricGate("p95_seconds", "max", 2.0),), critical_failures=("tenant_isolation",))
    assert result.passed is False
    assert [failure.reason for failure in result.failures] == ["critical regression reported", "3 does not satisfy <= 2"]
