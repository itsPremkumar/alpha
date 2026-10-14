"""Autonomous Command Lifecycle Engine for Alpha.

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
from typing import Any

from alpha.commands.registry import CommandExecutionResult, command_registry

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
    keywords: list[str]
    regex_patterns: list[str] = field(default_factory=list)
    priority: int = 50  # higher = higher priority


# Master lifecycle trigger rules mapping natural states & intents to the 418 slash commands
DEFAULT_TRIGGER_RULES: list[TriggerRule] = [
    # 1. PLANNING & GOAL DECOMPOSITION
    TriggerRule(
        rule_id="auto_goal_create",
        phase=LifecyclePhase.PLANNING,
        target_command="/goal create",
        description="Automatically decomposes complex long-term objectives into structured milestones",
        # `/goal create` starts a durable continuous loop (up to 50 iterations),
        # so an incidental noun such as "goal" or "objective" is not consent.
        # Require an explicit lifecycle verb before the goal/mission/loop noun.
        keywords=[],
        regex_patterns=[
            r"\b(?:create|start|launch|provision)\s+(?:(?:a|an|the)\s+)?(?:(?:new|autonomous|continuous|long[- ]running)\s+)*(?:goal|mission|loop)\b",
        ],
        priority=80,
    ),
    TriggerRule(
        rule_id="auto_plan_mode",
        phase=LifecyclePhase.PLANNING,
        target_command="/plan mode:auto",
        description="Automatically invokes 8-dimensional strategic meta-planner for multi-step architecture",
        keywords=["plan", "design", "architecture", "strategy", "design pattern", "system design", "spec", "specification", "from scratch", "build a complete", "build a full"],
        regex_patterns=[r"how\s+should\s+we\s+design", r"(?:design|create|build)\s+(?:an?\s+)?(?:\w+\s+)?(?:architecture|system)"],
        priority=75,
    ),
    # 2. SWARM & MULTI-AGENT ORCHESTRATION
    TriggerRule(
        rule_id="auto_swarm_create",
        phase=LifecyclePhase.SWARM,
        target_command="/swarm create",
        description="Automatically provisions a multi-agent specialized swarm for distributed workloads",
        # Swarm provisioning is a persistent orchestration action. Mentioning
        # subagents, teams, workers, or swarms is not consent to create one;
        # only an explicit creation verb should trigger this command.
        keywords=[],
        regex_patterns=[r"(?:use|spawn|start|create|launch|provision|deploy)\s+(?:(?:a|the)\s+)?(?:swarm|multi-agent(?:\s+swarm)?|team)"],
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
        rule_id="auto_verify_tests",
        phase=LifecyclePhase.VERIFICATION,
        target_command="/verify tests",
        description="Automatically executes test suites, type checkers, and semantic invariants",
        # Treat verification as an explicit action. A bare mention such as
        # "smoke test" in a task description must not hijack the whole run.
        keywords=[],
        regex_patterns=[
            r"\b(?:run|execute)\s+(?:the\s+)?(?:tests?|pytest|test suite|typecheck)\b",
            r"\b(?:verify|validate|check)\s+(?:that\s+)?(?:it|this|invariants|(?:all|the)\s+(?:tests?|invariants|build|code|changes?|results?))\b",
            r"\bcheck\s+if\s+it\s+works?\b",
        ],
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
    execution_result: CommandExecutionResult | None = None
    autonomous_directives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
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

    def __init__(self, rules: list[TriggerRule] | None = None) -> None:
        self.rules: list[TriggerRule] = rules or list(DEFAULT_TRIGGER_RULES)

    def add_rule(self, rule: TriggerRule) -> None:
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def identify_and_trigger(
        self,
        prompt: str,
        phase_hint: LifecyclePhase | None = None,
        context: dict[str, Any] | None = None,
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
        verification_negated = bool(
            re.search(
                r"\b(?:do not|don't|dont|never|avoid|without|no need to)\s+(?:run|execute|verify|validate|check|test)\b",
                text_lower,
            )
        )
        best_rule: TriggerRule | None = None
        best_score = 0.0
        best_reason = ""

        # Scan rules
        for rule in sorted(self.rules, key=lambda r: r.priority, reverse=True):
            if rule.rule_id == "auto_verify_tests" and verification_negated:
                continue
            if rule.rule_id == "auto_goal_create" and re.search(
                r"\b(?:do not|don't|dont|never|avoid|without|no need to)\s+(?:create|start|launch|provision)\b",
                text_lower,
            ):
                continue
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
            # Keywords are natural-language tokens, not substrings. Without
            # boundaries, mentioning the read-only tool `verify_command_approval`
            # or the noun `verification` accidentally launches a verify command.
            matched_kw = [kw for kw in rule.keywords if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text_lower)]
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
            directives: list[str] = [f"[AUTONOMOUS TRIGGER] Automatically identified '{target_cmd}' ({best_rule.phase.value}) based on: {best_reason}."]

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
        context: dict[str, Any] | None = None,
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
