"""Unit tests for DeepPerformanceAgent."""

from alpha.subagents.builtins.deep_performance_agent import (
    DEEP_PERFORMANCE_AGENT_CONFIG,
    DeepPerformanceAgent,
)


class TestDeepPerformanceAgent:
    def test_config(self):
        assert DEEP_PERFORMANCE_AGENT_CONFIG.name == "deep-performance"

    def test_profile_callable(self):
        agent = DeepPerformanceAgent()
        result = agent.profile_callable(lambda: sum(range(100)))
        assert result["elapsed_seconds"] >= 0.0
        assert result["top_stats"]

    def test_bottleneck_detection(self):
        agent = DeepPerformanceAgent()
        findings = agent.detect_bottlenecks("for i in x:\n  for j in y:\n    db.execute(q)")
        assert len(findings) >= 1

    def test_benchmark_comparison(self):
        agent = DeepPerformanceAgent()
        result = agent.benchmark_comparison(lambda: sum(range(10)), lambda: sum(range(10)))
        assert "speedup" in result
        assert result["speedup"] > 0

    def test_optimize_returns_contract(self):
        agent = DeepPerformanceAgent()
        contract = agent.optimize("for i in items:\n  results.append(work(i))", session_id="s-perf")
        assert contract.is_success()
        assert contract.session_id == "s-perf"
        # no profiler or benchmark ran in optimize(): no derived patch, no
        # passed oracle, no profile stamp, no speedup claim
        assert contract.unified_diff == ""
        assert contract.test_oracles == []
        assert contract.security_stamps == []
        assert "no speedup claim" in contract.executive_summary
