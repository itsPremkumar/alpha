"""Unit tests for DeepArchitectAgent."""

from alpha.subagents.builtins.deep_architect_agent import (
    DEEP_ARCHITECT_AGENT_CONFIG,
    DeepArchitectAgent,
)


class TestDeepArchitectAgent:
    def test_config_registered(self):
        assert DEEP_ARCHITECT_AGENT_CONFIG.name == "deep-architect"
        assert "task" in DEEP_ARCHITECT_AGENT_CONFIG.disallowed_tools

    def test_dependency_graph_and_cycles(self):
        agent = DeepArchitectAgent()
        graph = agent.build_dependency_graph(["missing-a.py", "missing-b.py"])
        assert len(graph.nodes) == 2
        assert agent.detect_circular_imports(graph) == []

    def test_cycle_detection(self):
        agent = DeepArchitectAgent()
        graph = agent.build_dependency_graph([])
        graph.nodes = ["a", "b"]
        graph.edges = [("a", "b"), ("b", "a")]
        cycles = agent.detect_circular_imports(graph)
        assert len(cycles) == 1

    def test_execute_returns_success_contract(self):
        agent = DeepArchitectAgent()
        contract = agent.execute("Split oversized module", ["a.py"], session_id="s-arch")
        assert contract.is_success()
        assert contract.session_id == "s-arch"
        assert len(contract.to_parent_text()) <= 4100
