"""Deep Debugger Agent for autonomous root-cause analysis.

Reproduces failures in isolation, inspects crash frames, applies spectrum-based
fault localization heuristics, and synthesizes minimal candidate patches.
Reproduction or canary checks are only claimed when they actually run.
"""

from __future__ import annotations

import traceback
from typing import Any

from alpha.subagents.config import SubagentConfig
from alpha.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    estimate_tokens,
)

AGENT_TYPE = "debugger"
DISPLAY_NAME = "DeepDebuggerAgent"

SYSTEM_PROMPT = """You are DeepDebuggerAgent, an autonomous root-cause analysis specialist.
Reproduce bugs in isolation, inspect variable states at crash frames, apply
spectrum-based fault localization, and synthesize minimal candidate patches with
canary tests. Never request human confirmation. Return a verified patch and a
compact root-cause post-mortem."""

DEEP_DEBUGGER_AGENT_CONFIG = SubagentConfig(
    name="deep-debugger",
    description="Autonomous root-cause analysis with fault localization and candidate patch synthesis.",
    system_prompt=SYSTEM_PROMPT,
    tools=["read_file", "bash", "python_repl_tool"],
    disallowed_tools=["task", "ralph_loop", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=120,
    timeout_seconds=1800,
)


class DeepDebuggerAgent:
    """Autonomous debugging specialist."""

    agent_type = AGENT_TYPE
    display_name = DISPLAY_NAME

    def localize_fault(self, failure_report: str, candidate_lines: list[str]) -> list[dict[str, Any]]:
        """Rank candidate faulty lines with a lightweight SBFL heuristic.

        Args:
            failure_report: Failure report or traceback text.
            candidate_lines: Candidate source lines.

        Returns:
            Ranked candidate list with suspiciousness scores.
        """
        keywords = {word.strip("(),:;.'\"").lower() for word in failure_report.split() if len(word) > 3}
        ranked: list[dict[str, Any]] = []
        for index, line in enumerate(candidate_lines):
            lowered = line.lower()
            hits = sum(1 for keyword in keywords if keyword and keyword in lowered)
            score = min(1.0, 0.2 + 0.2 * hits + (0.1 if ("none" in lowered or "null" in lowered) else 0.0))
            ranked.append({"line_index": index, "line": line[:300], "suspiciousness": round(score, 3)})
        ranked.sort(key=lambda item: item["suspiciousness"], reverse=True)
        return ranked

    def synthesize_patch(self, faulty_line: str, suggestion: str = "") -> str:
        """Synthesize a minimal unified style patch snippet.

        Args:
            faulty_line: Faulty source line.
            suggestion: Replacement hint.

        Returns:
            Minimal patch text.
        """
        replacement = suggestion or f"# TODO(auto-fix): guard faulty expression: {faulty_line[:120]}"
        return f"--- a/target.py\n+++ b/target.py\n@@\n-{faulty_line[:300]}\n+{replacement[:300]}\n"

    def diagnose(self, failure_report: str, candidate_lines: list[str], session_id: str = "") -> DeepHandoffContract:
        """Run autonomous diagnosis and return synthesis.

        Args:
            failure_report: Failure report text.
            candidate_lines: Candidate source lines.
            session_id: Isolated session identifier.

        Returns:
            Compact handoff contract with patch and post-mortem.
        """
        try:
            ranked = self.localize_fault(failure_report, candidate_lines)
            top = ranked[0] if ranked else {"line": "", "suspiciousness": 0.0}
            patch = self.synthesize_patch(str(top.get("line", "")))
            summary = (
                "DeepDebuggerAgent ranked "
                f"{len(ranked)} candidate statement(s) with spectrum-based heuristics against the "
                f"provided failure report and synthesized a minimal candidate patch targeting "
                f"suspiciousness {top.get('suspiciousness', 0.0)}. No failure reproduction or "
                "canary test was executed in this run, so the patch remains unverified."
            )
            contract = DeepHandoffContract(
                status=DeepExecutionStatus.SUCCESS,
                executive_summary=summary,
                unified_diff=patch,
                invariant_assertions=[
                    "fault localization is heuristic over the provided candidate lines",
                    "candidate patch targets only the highest-ranked suspicious line",
                    "no reproduction or canary test executed; patch unverified",
                ],
                session_id=session_id,
                agent_type=self.agent_type,
            )
        except Exception as exc:
            contract = DeepHandoffContract(
                status=DeepExecutionStatus.UNRECOVERABLE_ERROR,
                executive_summary="Debugging run failed without recoverable progress.",
                error_detail="".join(traceback.format_exception_only(type(exc), exc))[:1000],
                session_id=session_id,
                agent_type=self.agent_type,
            )
        contract.tokens_returned = max(1, estimate_tokens(contract.to_parent_text()))
        return contract
