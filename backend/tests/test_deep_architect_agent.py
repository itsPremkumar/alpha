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
        # missing files are recorded as unparsed so claims stay scoped
        assert graph.parsed_files == []
        assert graph.unparsed_files == ["missing-a.py", "missing-b.py"]

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
        # "a.py" does not exist: ast.parse could not run on it, so the oracle
        # must not claim a pass and no import-boundary stamp may be issued.
        assert contract.test_oracles
        assert contract.test_oracles[0]["passed"] is False
        assert contract.security_stamps == []

    def test_execute_claims_pass_only_for_parsed_targets(self, tmp_path):
        target = tmp_path / "module.py"
        target.write_text("import os\n\n\ndef run():\n    return os.getcwd()\n", encoding="utf-8")
        agent = DeepArchitectAgent()
        contract = agent.execute("Split oversized module", [str(target)], session_id="s-arch-2")
        assert contract.is_success()
        # the parse check genuinely ran over the only target file
        assert contract.test_oracles[0]["passed"] is True
        assert "architect:import-acyclicity-checked" in contract.security_stamps
