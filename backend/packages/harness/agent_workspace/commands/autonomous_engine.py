"""Autonomous Command Lifecycle Engine for Agent Workspace.

Automatically identifies the exact required slash command based on:
1. User intent & semantic task requirements
2. Current execution phase (PLANNING -> RESEARCH -> SWARM -> CODING -> VERIFY -> HEAL -> LEARN)
3. Runtime triggers (errors, code edits, completion signals, cron/schedule)

Executes commands autonomously at the correct time without requiring manual user typing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from agent_workspace.commands.registry import CommandCategory, CommandExecutionResult, command_registry

logger = logging.getLogger(__name__)


class LifecyclePhase(str, Enum):
    PLANNING = "planning"
    RESEARCH = "research"
    SWARM = "swarm"
    CODING = "coding"
    VERIFICATION = "verification"
    SELF_HEAL = "self_heal"
    REFLECTION = "reflection"
    SCHEDULE = "schedule"
    GENERAL = "general"


@dataclass
class TriggerRule:
    rule_id: str
    phase: LifecyclePhase
    target_command: str
    description: str
    keywords: List[str]
    regex_patterns: List[str] = field(default_factory=list)
    priority: int = 50  # higher = higher priority


# Master lifecycle trigger rules mapping natural states & intents to the 418 slash commands
DEFAULT_TRIGGER_RULES: List[TriggerRule] = [
    # 1. PLANNING & GOAL DECOMPOSITION
    TriggerRule(
        rule_id="auto_goal_create",
        phase=LifecyclePhase.PLANNING,
        target_command="/goal create",
        description="Automatically decomposes complex long-term objectives into structured milestones",
        keywords=["goal", "objective", "mission", "milestone", "end-to-end", "from scratch", "build a complete", "roadmap"],
        regex_patterns=[r"(?:build|create|develop|implement)\s+(?:a|an)\s+(?:complete|full|entire)", r"(?:my|our)\s+goal\s+is"],
        priority=80,
    ),
    TriggerRule(
        rule_id="auto_plan_mode",
        phase=LifecyclePhase.PLANNING,
        target_command="/plan mode:auto",
        description="Automatically invokes 8-dimensional strategic meta-planner for multi-step architecture",
        keywords=["plan", "design", "architecture", "strategy", "design pattern", "system design", "spec", "specification"],
        regex_patterns=[r"how\s+should\s+we\s+design", r"(?:design|create|build)\s+(?:an?\s+)?(?:\w+\s+)?(?:architecture|system)"],
        priority=75,
    ),
    # 2. SWARM & MULTI-AGENT ORCHESTRATION
    TriggerRule(
        rule_id="auto_swarm_create",
        phase=LifecyclePhase.SWARM,
        target_command="/swarm create",
        description="Automatically provisions a multi-agent specialized swarm for distributed workloads",
        keywords=["swarm", "multi-agent", "team", "parallel agents", "subagents", "workers", "collaborate", "consensus"],
        regex_patterns=[r"(?:use|spawn|start)\s+(?:a\s+)?(?:swarm|team|multi-agent)"],
        priority=85,
    ),
    # 3. RESEARCH & DEEP SEARCH
    TriggerRule(
        rule_id="auto_research_deep",
        phase=LifecyclePhase.RESEARCH,
        target_command="/research deep",
        description="Automatically launches multi-hop deep web and codebase evidence retrieval",
        keywords=["research", "investigate", "find papers", "state of the art", "compare solutions", "crawl", "documentation for", "how does"],
        regex_patterns=[r"research\s+(?:about|on|into)", r"look\s+up\s+the\s+latest"],
        priority=70,
    ),
    # 4. CODING & REFACTORING
    TriggerRule(
        rule_id="auto_code_refactor",
        phase=LifecyclePhase.CODING,
        target_command="/code refactor",
        description="Automatically analyzes AST and restructures code modules cleanly",
        keywords=["refactor", "restructure", "clean up code", "modularize", "rewrite function", "optimize code"],
        regex_patterns=[r"refactor\s+(?:the\s+)?\w+", r"clean\s+up\s+(?:the\s+)?code"],
        priority=65,
    ),
    # 5. VERIFICATION & LINTING (Runs automatically post-edit or when requested)
    TriggerRule(
        rule_id="auto_verify_all",
        phase=LifecyclePhase.VERIFICATION,
        target_command="/verify all",
        description="Automatically executes test suites, type checkers, and semantic invariants",
        keywords=["verify", "test", "run tests", "pytest", "check invariants", "unit tests", "typecheck", "validate code", "proof"],
        regex_patterns=[r"run\s+(?:the\s+)?tests?", r"check\s+if\s+it\s+works?"],
        priority=90,
    ),
    # 6. SELF-HEALING & INCIDENT RECOVERY (Runs automatically upon error or crash)
    TriggerRule(
        rule_id="auto_self_heal",
        phase=LifecyclePhase.SELF_HEAL,
        target_command="/self-heal",
        description="Automatically catches runtime errors, mutates strategy, and applies fixes",
        keywords=["error", "exception", "traceback", "failed", "bug", "crash", "broken", "fix error", "repair", "remedy"],
        regex_patterns=[r"(?:fix|resolve)\s+(?:the\s+)?(?:error|bug|issue)", r"traceback\s+\(most\s+recent"],
        priority=95,
    ),
    # 7. REFLECTION & RECURSIVE SELF-IMPROVEMENT (Runs on task completion)
    TriggerRule(
        rule_id="auto_learn_save",
        phase=LifecyclePhase.REFLECTION,
        target_command="/learn save",
        description="Automatically reflects on execution trajectory and updates persistent memory heuristics",
        keywords=["remember this", "save learning", "note for future", "reflect", "good job", "lesson learned", "persist heuristic"],
        regex_patterns=[r"(?:remember|save)\s+(?:this\s+)?(?:rule|lesson|pattern)"],
        priority=60,
    ),
    # 8. CRON & SCHEDULING
    TriggerRule(
        rule_id="auto_cron_schedule",
        phase=LifecyclePhase.SCHEDULE,
        target_command="/cron create",
        description="Automatically registers recurring background timers and cron heartbeat jobs",
        keywords=["schedule", "cron", "every day", "every hour", "every 5 minutes", "recurring", "interval timer"],
        regex_patterns=[r"every\s+\d+\s+(?:minutes?|hours?|days?)", r"run\s+(?:this\s+)?on\s+a\s+schedule"],
        priority=70,
    ),
]


@dataclass
class AutonomousDetectionResult:
    matched: bool
    command: str
    phase: LifecyclePhase
    confidence: float
    reason: str
    rule_id: str
    execution_result: Optional[CommandExecutionResult] = None
    autonomous_directives: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched": self.matched,
            "command": self.command,
            "phase": self.phase.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "autonomous_directives": self.autonomous_directives,
            "execution_result": self.execution_result.to_dict() if self.execution_result else None,
        }


class AutonomousCommandEngine:
    """Intelligent intent detection & real-time lifecycle trigger coordinator."""

    def __init__(self, rules: Optional[List[TriggerRule]] = None) -> None:
        self.rules: List[TriggerRule] = rules or list(DEFAULT_TRIGGER_RULES)

    def add_rule(self, rule: TriggerRule) -> None:
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def identify_and_trigger(
        self,
        prompt: str,
        phase_hint: Optional[LifecyclePhase] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
    ) -> AutonomousDetectionResult:
        """Analyzes text or runtime signals, identifies matching slash command, and executes it."""
        text = prompt.strip()
        if not text:
            return AutonomousDetectionResult(
                matched=False,
                command="",
                phase=LifecyclePhase.GENERAL,
                confidence=0.0,
                reason="Empty prompt.",
                rule_id="",
            )

        # If user explicitly entered a slash command, execute directly
        if text.startswith("/"):
            exec_res = command_registry.execute(text, context=context) if auto_execute else None
            directives = exec_res.autonomous_directives if exec_res else []
            return AutonomousDetectionResult(
                matched=True,
                command=text.split()[0],
                phase=LifecyclePhase.GENERAL,
                confidence=1.0,
                reason="Explicit user slash command entered.",
                rule_id="explicit_command",
                execution_result=exec_res,
                autonomous_directives=directives,
            )

        text_lower = text.lower()
        best_rule: Optional[TriggerRule] = None
        best_score = 0.0
        best_reason = ""

        # Scan rules
        for rule in sorted(self.rules, key=lambda r: r.priority, reverse=True):
            score = 0.0

            # Check phase hint bias
            if phase_hint and rule.phase == phase_hint:
                score += 0.3

            # Check regex patterns
            for pattern in rule.regex_patterns:
                if re.search(pattern, text_lower):
                    score += 0.5
                    best_reason = f"Matched pattern '{pattern}'"
                    break

            # Check keyword matches
            matched_kw = [kw for kw in rule.keywords if kw in text_lower]
            if matched_kw:
                score += min(0.5, len(matched_kw) * 0.25)
                if not best_reason:
                    best_reason = f"Matched keyword(s): {', '.join(matched_kw)}"

            # Factor in rule priority weight
            weighted_score = score * (rule.priority / 100.0)
            if weighted_score > best_score:
                best_score = weighted_score
                best_rule = rule

        # Threshold for automatic activation
        if best_rule and best_score >= 0.15:
            target_cmd = best_rule.target_command
            # Automatically compose command with user args
            cmd_with_args = f"{target_cmd} {text}"
            exec_res = None
            directives: List[str] = [f"[AUTONOMOUS TRIGGER] Automatically identified '{target_cmd}' ({best_rule.phase.value}) based on: {best_reason}."]

            if auto_execute:
                exec_res = command_registry.execute(cmd_with_args, context=context)
                if exec_res.autonomous_directives:
                    directives.extend(exec_res.autonomous_directives)

            return AutonomousDetectionResult(
                matched=True,
                command=target_cmd,
                phase=best_rule.phase,
                confidence=min(1.0, round(best_score * 1.5, 2)),
                reason=best_reason,
                rule_id=best_rule.rule_id,
                execution_result=exec_res,
                autonomous_directives=directives,
            )

        return AutonomousDetectionResult(
            matched=False,
            command="",
            phase=LifecyclePhase.GENERAL,
            confidence=0.0,
            reason="Standard conversational intent (no special slash command triggered).",
            rule_id="none",
        )

    def trigger_phase_transition(
        self,
        phase: LifecyclePhase,
        details: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> AutonomousDetectionResult:
        """Trigger phase-specific slash commands at exact lifecycle events (e.g. on error, post-code)."""
        matching_rules = [r for r in self.rules if r.phase == phase]
        if not matching_rules:
            return AutonomousDetectionResult(
                matched=False,
                command="",
                phase=phase,
                confidence=0.0,
                reason=f"No rules for phase {phase.value}",
                rule_id="",
            )

        rule = matching_rules[0]
        cmd = f"{rule.target_command} {details}".strip()
        exec_res = command_registry.execute(cmd, context=context)
        directives = [f"[AUTONOMOUS PHASE TRANSITION: {phase.value.upper()}] Automatically executing '{rule.target_command}'."]
        if exec_res.autonomous_directives:
            directives.extend(exec_res.autonomous_directives)

        return AutonomousDetectionResult(
            matched=True,
            command=rule.target_command,
            phase=phase,
            confidence=1.0,
            reason=f"Lifecycle transition to {phase.value}",
            rule_id=rule.rule_id,
            execution_result=exec_res,
            autonomous_directives=directives,
        )


# Global singleton engine
autonomous_command_engine = AutonomousCommandEngine()
