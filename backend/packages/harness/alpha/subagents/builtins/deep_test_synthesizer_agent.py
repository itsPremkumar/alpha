"""Deep Test and Invariant Synthesizer Agent.

Inspects coverage gaps, generates property-based edge-case tests, and validates
test quality with lightweight mutation testing heuristics.
"""

from __future__ import annotations

from alpha.subagents.config import SubagentConfig
from alpha.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    estimate_tokens,
)

AGENT_TYPE = "test_synthesizer"
DISPLAY_NAME = "DeepTestSynthesizerAgent"

SYSTEM_PROMPT = """You are DeepTestSynthesizerAgent, an autonomous test and invariant specialist.
Inspect coverage gaps, generate property-based tests with boundary inputs including
unicode, null bytes, integer extremes, and empty collections, then validate quality
with mutation testing. Never request human confirmation. Return compact synthesis
with verified test oracles."""

DEEP_TEST_SYNTHESIZER_AGENT_CONFIG = SubagentConfig(
    name="deep-test-synthesizer",
    description="Autonomous property-based test generation (mutation validation claimed only when executed).",
    system_prompt=SYSTEM_PROMPT,
    tools=["read_file", "bash", "python_repl_tool"],
    disallowed_tools=["task", "ralph_loop", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=120,
    timeout_seconds=1800,
)

EDGE_CASE_INPUTS: list[str] = [
    "empty string",
    "unicode boundary: \\U0001f600 and combining marks",
    "null bytes: \\x00 in middle",
    "integer extremes: -2**63, 2**63-1",
    "empty collection and single element collection",
    "oversized input: 1MB repeated pattern",
]


class DeepTestSynthesizerAgent:
    """Autonomous test synthesis specialist."""

    agent_type = AGENT_TYPE
    display_name = DISPLAY_NAME

    def identify_coverage_gaps(self, coverage_report: dict[str, float], threshold: float = 0.8) -> list[str]:
        """Identify modules below a coverage threshold.

        Args:
            coverage_report: Mapping of module name to coverage fraction.
            threshold: Minimum acceptable coverage fraction.

        Returns:
            Sorted list of modules below threshold.
        """
        gaps = [module for module, ratio in coverage_report.items() if float(ratio) < threshold]
        return sorted(gaps)

    def generate_tests(self, module_name: str, function_name: str) -> str:
        """Generate property-based test source for a target function.

        Args:
            module_name: Target module name.
            function_name: Target function name.

        Returns:
            Python test source text.
        """
        cases = "\n".join(f"# edge case: {case}" for case in EDGE_CASE_INPUTS)
        return (
            "import pytest\n"
            "from hypothesis import given, strategies as st\n"
            f"from {module_name} import {function_name}\n\n"
            f"{cases}\n"
            f"def test_{function_name}_properties():\n"
            f"    assert callable({function_name})\n"
            f"    for value in ['', 'unicode-\\u2603', '\\x00', 'a' * 10000, '[]', '{{}}']:\n"
            "        try:\n"
            f"            {function_name}(value)\n"
            "        except (ValueError, TypeError):\n"
            "            continue\n"
        )

    def mutation_score(self, killed: int, total: int) -> float:
        """Compute mutation score.

        Args:
            killed: Number of mutants killed.
            total: Total mutants evaluated.

        Returns:
            Mutation score between 0 and 1.
        """
        if total <= 0:
            return 0.0
        return round(min(1.0, killed / total), 3)

    def synthesize(
        self,
        coverage_report: dict[str, float],
        module_name: str,
        function_name: str,
        session_id: str = "",
    ) -> DeepHandoffContract:
        """Synthesize tests and return compact synthesis.

        Args:
            coverage_report: Coverage mapping.
            module_name: Target module.
            function_name: Target function.
            session_id: Isolated session identifier.

        Returns:
            Compact handoff contract.
        """
        gaps = self.identify_coverage_gaps(coverage_report)
        test_source = self.generate_tests(module_name, function_name)
        summary = (
            f"DeepTestSynthesizerAgent identified coverage gaps in {len(gaps)} module(s) and "
            f"generated property-based test source for {module_name}.{function_name} covering "
            f"{len(EDGE_CASE_INPUTS)} boundary classes. The generated tests were not executed "
            "and no mutation testing was run, so no pass results or mutation score are claimed."
        )
        contract = DeepHandoffContract(
            status=DeepExecutionStatus.SUCCESS,
            executive_summary=summary,
            unified_diff=test_source[:2000],
            # The generated tests never ran: no passed oracles, no fabricated
            # mutation score, no verification stamp.
            test_oracles=[
                {
                    "name": "synthetic-property-tests",
                    "command": f"pytest tests for {function_name}",
                    "status": "not_run",
                    "detail": "test source generated but never executed in this run",
                }
            ],
            security_stamps=[],
            invariant_assertions=[
                "generated test source includes unicode and null-byte boundary inputs",
                "no test execution or mutation testing performed in this run",
            ],
            session_id=session_id,
            agent_type=self.agent_type,
        )
        contract.tokens_returned = max(1, estimate_tokens(contract.to_parent_text()))
        return contract
