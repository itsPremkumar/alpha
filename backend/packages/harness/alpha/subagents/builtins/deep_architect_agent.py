"""Deep Architect Agent for multi-file refactoring.

Analyzes repository-wide imports, resolves circular dependencies, partitions
oversized modules, and produces atomic multi-file refactoring plans with
dependency impact graphs. All decisions are autonomous.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.subagents.config import SubagentConfig
from alpha.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    estimate_tokens,
)

AGENT_TYPE = "architect"
DISPLAY_NAME = "DeepArchitectAgent"

SYSTEM_PROMPT = """You are DeepArchitectAgent, an autonomous multi-file refactoring specialist.
Work in an isolated context. Analyze repository-wide abstract syntax trees, resolve
circular imports, partition oversized modules, and generate dependency impact graphs
before editing. Execute atomic refactoring steps while keeping imports and symbols
intact. Never request human confirmation; resolve every branch autonomously.
Return only compact synthesis with a minimal unified diff and verified checks."""

DEEP_ARCHITECT_AGENT_CONFIG = SubagentConfig(
    name="deep-architect",
    description="Autonomous multi-file refactoring specialist with AST impact analysis and atomic edits (claims scoped to parsed targets).",
    system_prompt=SYSTEM_PROMPT,
    tools=["read_file", "bash", "ast_grep_search"],
    disallowed_tools=["task", "ralph_loop", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=120,
    timeout_seconds=1800,
)


@dataclass
class DependencyImpactGraph:
    """Dependency impact graph for a refactoring plan.

    Tracks which targeted files were genuinely parsed so downstream claims
    only cover inputs that were actually observed.
    """

    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    parsed_files: list[str] = field(default_factory=list)
    unparsed_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable dictionary.

        Returns:
            Graph dictionary including parse coverage of targeted files.
        """
        return {
            "nodes": list(self.nodes),
            "edges": [list(e) for e in self.edges],
            "parsed_files": list(self.parsed_files),
            "unparsed_files": list(self.unparsed_files),
        }


class DeepArchitectAgent:
    """Autonomous multi-file refactoring specialist."""

    agent_type = AGENT_TYPE
    display_name = DISPLAY_NAME

    def build_dependency_graph(self, file_paths: list[str]) -> DependencyImpactGraph:
        """Build a local import dependency graph using AST parsing.

        Args:
            file_paths: Python file paths to analyze.

        Returns:
            Dependency impact graph.
        """
        graph = DependencyImpactGraph()
        for raw in file_paths:
            path = Path(raw)
            graph.nodes.append(str(raw))
            if not path.is_file():
                graph.unparsed_files.append(str(raw))
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                graph.unparsed_files.append(str(raw))
                continue
            graph.parsed_files.append(str(raw))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        graph.edges.append((str(raw), alias.name))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    graph.edges.append((str(raw), module))
        return graph

    def detect_circular_imports(self, graph: DependencyImpactGraph) -> list[list[str]]:
        """Detect simple cycles in the dependency graph.

        Args:
            graph: Dependency impact graph.

        Returns:
            List of detected cycles (each a list of node names).
        """
        adjacency: dict[str, list[str]] = {}
        for source, target in graph.edges:
            adjacency.setdefault(source, []).append(target)
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def visit(node: str) -> None:
            visited.add(node)
            stack.append(node)
            on_stack.add(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in visited:
                    visit(neighbor)
                elif neighbor in on_stack:
                    start = stack.index(neighbor)
                    cycles.append(stack[start:] + [neighbor])
            stack.pop()
            on_stack.discard(node)

        for node in graph.nodes:
            if node not in visited:
                visit(node)
        return cycles

    def plan_refactoring(self, goal: str, target_files: list[str]) -> dict[str, Any]:
        """Create an autonomous refactoring plan.

        Args:
            goal: Refactoring goal.
            target_files: Targeted file paths.

        Returns:
            Plan dictionary with impact graph and steps.
        """
        graph = self.build_dependency_graph(target_files)
        cycles = self.detect_circular_imports(graph)
        steps = [
            {"step": 1, "action": "parse ASTs for all targeted modules"},
            {"step": 2, "action": "resolve circular imports" if cycles else "verify import acyclicity"},
            {"step": 3, "action": "partition oversized modules behind stable interfaces"},
            {"step": 4, "action": "apply atomic edits and re-verify imports"},
        ]
        return {
            "goal": goal,
            "impact_graph": graph.to_dict(),
            "circular_imports": cycles,
            "steps": steps,
        }

    def execute(self, goal: str, target_files: list[str], session_id: str = "") -> DeepHandoffContract:
        """Execute autonomous refactoring analysis and return synthesis.

        Args:
            goal: Refactoring goal.
            target_files: Targeted file paths.
            session_id: Isolated session identifier.

        Returns:
            Compact handoff contract.
        """
        plan = self.plan_refactoring(goal, target_files)
        graph = plan["impact_graph"]
        target_count = len(target_files)
        parsed_count = len(graph["parsed_files"])
        unparsed_count = len(graph["unparsed_files"])
        summary = (
            f"DeepArchitectAgent analyzed {target_count} module(s) ({parsed_count} parsed, "
            f"{unparsed_count} unparsed) and generated an atomic refactoring plan with "
            f"{len(graph['edges'])} dependency edge(s) and "
            f"{len(plan['circular_imports'])} circular import cycle(s). The plan is a proposal: "
            "no edits were applied, so symbol preservation under the partitioning is "
            "unverified by execution."
        )
        # Only claim the ast.parse oracle when it genuinely ran over every
        # targeted module; otherwise report the observed shortfall honestly.
        test_oracles: list[dict[str, Any]] = []
        if target_count:
            test_oracles.append(
                {
                    "name": "ast-parse",
                    "command": "ast.parse targeted modules",
                    "passed": unparsed_count == 0,
                    "detail": f"{parsed_count}/{target_count} target module(s) parsed",
                }
            )
        # An import-boundary stamp may only be issued when the acyclicity
        # check actually covered all targeted files.
        security_stamps = ["architect:import-acyclicity-checked"] if (target_count and not unparsed_count) else []
        contract = DeepHandoffContract(
            status=DeepExecutionStatus.SUCCESS,
            executive_summary=summary,
            test_oracles=test_oracles,
            security_stamps=security_stamps,
            invariant_assertions=[
                f"dependency graph built from {parsed_count} of {target_count} targeted file(s)",
                f"{len(plan['circular_imports'])} circular import cycle(s) observed in parsed graph",
                "no edits applied; partitioning plan remains a proposal",
                "parent received synthesis only",
            ],
            session_id=session_id,
            agent_type=self.agent_type,
        )
        contract.tokens_returned = max(1, estimate_tokens(contract.to_parent_text()))
        return contract
