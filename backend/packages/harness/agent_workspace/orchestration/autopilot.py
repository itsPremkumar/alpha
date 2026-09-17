"""Autonomous Executive Autopilot: Zero-Config Intent & Preset Dispatcher.

Synthesized from:
- Autonomous Self-Configuring ASI Harness v2 (Section 1 & 2)
- ASI-Level Universal Agent Harness Architecture (Section 48: Prompt-to-Completion Flow)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AutopilotPlan:
    """Structured classification output produced by the Executive Autopilot."""

    intent: str
    difficulty: str
    recommended_preset: str
    requires_subagents: bool = False
    requires_tools: list[str] = field(default_factory=list)
    suggested_discipline: str = "general"
    confidence: float = 0.9
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutiveAutopilot:
    """Autonomous executive that determines difficulty, intent, and optimal runtime presets."""

    # Pattern definitions for domain intent detection
    CODING_PATTERNS = [
        r"\b(code|coding|function|class|method|refactor|debug|bug|fix|implement|repo|repository|ast|git|commit)\b",
        r"\b(python|typescript|javascript|rust|golang|c\+\+|react|next\.js|fastapi|pytest|pnpm|npm)\b",
        r"\.(py|ts|tsx|js|jsx|rs|go|cpp|c|h|json|yaml|toml|sql|sh|ps1)\b",
        r"\b(traceback|syntaxerror|typeerror|assertionerror|unittest|test suite|dockerfile)\b",
    ]

    RESEARCH_PATTERNS = [
        r"\b(research|deep research|investigate|survey|market analysis|competitors|literature review)\b",
        r"\b(search the web|find sources|look up|citations|news|trends|current state|benchmark comparison)\b",
        r"\b(who is|what is the history of|summarize articles|find recent)\b",
    ]

    GOVERNANCE_PATTERNS = [
        r"\b(review|audit|security|boundary|permission|invariant|gap analysis|risk|vulnerability)\b",
        r"\b(deception|credential|redact|rubber-stamp|rubric|peer review|compliance)\b",
    ]

    MISSION_PATTERNS = [
        r"\b(mission|epic|roadmap|milestone|hierarchy|work queue|dag|schedule tasks|track progress)\b",
        r"\b(multi-phase|long-term goal|project management|subtasks|dependencies)\b",
    ]

    PLAN_ONLY_PATTERNS = [
        r"\b(plan only|do not execute|just plan|draft a plan|create an implementation plan|spec only)\b",
        r"\b(how would you design|propose an approach|do not modify files yet|architecture draft)\b",
    ]

    SWARM_PATTERNS = [
        r"\b(swarm|team of agents|multi-agent|group chat|council|parallel workers|delegate to agents)\b",
        r"\b(bot roster|agent society|debate between agents)\b",
    ]

    def __init__(self, default_preset: str = "deep_code"):
        self.default_preset = default_preset

    def classify_intent(self, prompt: str) -> AutopilotPlan:
        """Classify user intent, difficulty, and determine the optimal preset."""
        text = prompt.lower().strip()

        # Check Plan Only first
        if any(re.search(pat, text) for pat in self.PLAN_ONLY_PATTERNS):
            return AutopilotPlan(
                intent="planning",
                difficulty=self._estimate_difficulty(text),
                recommended_preset="plan",
                requires_subagents=False,
                requires_tools=["read_file", "glob", "grep", "ast_grep_search", "inspect_repo_twin"],
                suggested_discipline="architect",
                confidence=0.95,
                reasoning="User explicitly requested planning or architecture design without destructive execution.",
            )

        # Check Governance / Security Review
        if any(re.search(pat, text) for pat in self.GOVERNANCE_PATTERNS) and not any(re.search(pat, text) for pat in self.CODING_PATTERNS):
            return AutopilotPlan(
                intent="governance",
                difficulty=self._estimate_difficulty(text),
                recommended_preset="discipline",
                requires_subagents=False,
                requires_tools=["consult_plan_gap_analysis", "review_plan_invariant_gate", "astra_security_manage"],
                suggested_discipline="security_reviewer",
                confidence=0.90,
                reasoning="Request focuses on plan gap analysis, invariants, security audit, or quality verification.",
            )

        # Check Swarm / Multi-Agent
        if any(re.search(pat, text) for pat in self.SWARM_PATTERNS):
            return AutopilotPlan(
                intent="swarm",
                difficulty="complex",
                recommended_preset="autonomous_swarm",
                requires_subagents=True,
                requires_tools=["task", "agent_message", "group_chat", "kanban_board", "swarm_tool"],
                suggested_discipline="swarm_coordinator",
                confidence=0.92,
                reasoning="Request requires multi-agent coordination, swarm topologies, or parallel delegation.",
            )

        # Check Mission Hierarchy
        if any(re.search(pat, text) for pat in self.MISSION_PATTERNS) and len(text.split()) > 15:
            return AutopilotPlan(
                intent="mission",
                difficulty=self._estimate_difficulty(text),
                recommended_preset="mission_director",
                requires_subagents=True,
                requires_tools=["manage_mission_hierarchy", "schedule_work_queue", "trace_artifact_lineage"],
                suggested_discipline="mission_director",
                confidence=0.88,
                reasoning="Request involves high-level goal decomposition, multi-task DAGs, or work queues.",
            )

        # Check Deep Research
        if any(re.search(pat, text) for pat in self.RESEARCH_PATTERNS) and not any(re.search(pat, text) for pat in self.CODING_PATTERNS):
            return AutopilotPlan(
                intent="research",
                difficulty=self._estimate_difficulty(text),
                recommended_preset="research",
                requires_subagents=False,
                requires_tools=["web_search", "web_fetch", "compile_five_pass_search", "blackboard_query"],
                suggested_discipline="researcher",
                confidence=0.93,
                reasoning="Request is information-retrieval focused with web searching and evidence synthesis.",
            )

        # Check Coding / Implementation
        if any(re.search(pat, text) for pat in self.CODING_PATTERNS):
            diff = self._estimate_difficulty(text)
            return AutopilotPlan(
                intent="coding",
                difficulty=diff,
                recommended_preset="deep_code",
                requires_subagents=(diff in ("complex", "extreme")),
                requires_tools=[
                    "generate_repo_map",
                    "auto_test_and_repair",
                    "manage_code_checkpoint",
                    "read_file",
                    "write_file",
                    "str_replace",
                    "bash",
                ],
                suggested_discipline="software_engineer",
                confidence=0.95,
                reasoning="Request requires reading, analyzing, editing, testing, or repairing codebase files.",
            )

        # Fallback to standard
        return AutopilotPlan(
            intent="general",
            difficulty=self._estimate_difficulty(text),
            recommended_preset="standard",
            requires_subagents=False,
            confidence=0.85,
            reasoning="General interactive session; full toolset enabled.",
        )

    @classmethod
    def resolve_preset(cls, prompt: str, requested_preset: str | None = None) -> str:
        """Resolve preset, respecting user choice if explicit, otherwise using Autopilot."""
        if requested_preset and requested_preset.lower() not in ("auto", "default", ""):
            return requested_preset.lower()

        plan = cls().classify_intent(prompt)
        return plan.recommended_preset

    def _estimate_difficulty(self, text: str) -> str:
        words = len(text.split())
        lines = len(text.splitlines())
        has_multiple_requirements = (
            text.count(" and ") + text.count("\n-") + text.count("\n1.") + text.count("also")
        ) >= 3

        if words > 100 or lines > 10 or has_multiple_requirements:
            return "complex"
        if words > 30 or lines > 3:
            return "moderate"
        return "trivial"
