"""TheoryOfMindConsultant: Synthesizes implicit human user mental models and expectations."""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_workspace.reasoning.tom.models import (
    IntentHypothesis,
    PriorityDomain,
    RiskTolerance,
)

logger = logging.getLogger(__name__)


def _calibrate_confidence(
    task_description: str,
    workspace_context: str | None,
    context_metadata: dict[str, Any] | None,
) -> float:
    """Calibrated confidence instead of a hardcoded 0.90.

    Starts at 0.90, penalizes ambiguity/emptiness, rewards grounding context.
    Clamped to [0.50, 0.95] so callers can distinguish certain vs thin reads.
    """
    confidence = 0.90
    text = task_description.strip()
    if len(text) < 20:
        confidence -= 0.15
    if re.search(r"(?i)\b(it|this|that|stuff|thing|better|optimize)\b", text) and len(text.split()) < 8:
        confidence -= 0.10
    if workspace_context and workspace_context.strip():
        confidence += 0.03
    if context_metadata:
        if context_metadata.get("repo_map") or context_metadata.get("file_list"):
            confidence += 0.02
        if context_metadata.get("prior_tasks"):
            confidence += 0.02
    return round(max(0.50, min(0.95, confidence)), 2)


class TheoryOfMindConsultant:
    """Cognitive modeler hypothesizing implicit human expectations, invariants, and failure modes."""

    def consult(
        self,
        task_description: str,
        workspace_context: str | None = None,
        context_metadata: dict[str, Any] | None = None,
    ) -> IntentHypothesis:
        text = task_description.lower()
        priorities: list[PriorityDomain] = []
        unstated_expectations: list[str] = []
        pitfalls: list[str] = []
        constraints: list[str] = []
        risk_tolerance = RiskTolerance.MEDIUM

        # 1. Detect Task Intent & Invariants
        if "refactor" in text or "clean" in text or "simplify" in text:
            priorities.extend([PriorityDomain.BACKWARD_COMPATIBILITY, PriorityDomain.CORRECTNESS, PriorityDomain.READABILITY])
            unstated_expectations.extend(
                [
                    "Maintain exact same behavior for all external callers and public APIs.",
                    "Preserve existing unit test suite compatibility; all tests must remain green.",
                    "Do not introduce unrelated code formatting or stylistic reordering outside the target module.",
                ]
            )
            pitfalls.append("Changing return types or method signatures that break downstream consumers.")
            constraints.append("Refactor in small verified increments rather than massive file rewrites.")

        elif "fix" in text or "bug" in text or "issue" in text or "error" in text:
            priorities.extend([PriorityDomain.CORRECTNESS, PriorityDomain.MINIMAL_DIFF, PriorityDomain.TEST_COVERAGE])
            unstated_expectations.extend(
                [
                    "Keep diff minimal and focused strictly on fixing the root cause.",
                    "Add a regression test reproducing the issue and proving the fix works.",
                    "Ensure no edge cases in adjacent code are broken by the patch.",
                ]
            )
            pitfalls.append("Attempting wide refactorings while fixing a localized bug.")
            constraints.append("Do not modify unrelated files; isolate the fix to the offending component.")

        elif "perf" in text or "fast" in text or "speed" in text or "optimize" in text:
            priorities.extend([PriorityDomain.PERFORMANCE, PriorityDomain.CORRECTNESS, PriorityDomain.EVIDENCE])
            unstated_expectations.extend(
                [
                    "Verify performance improvements empirically with before/after measurements.",
                    "Ensure algorithmic optimizations do not introduce concurrency or memory race conditions.",
                ]
            )
            pitfalls.append("Over-optimizing code at the expense of maintainability without measuring real bottlenecks.")
            constraints.append("Profile or benchmark before and after applying optimizations.")

        elif re.search(r"\b(research|survey|compare|investigate|landscape|literature)\b", text):
            priorities.extend([PriorityDomain.EVIDENCE, PriorityDomain.CORRECTNESS])
            unstated_expectations.extend(
                [
                    "Cite primary sources for every load-bearing claim; no uncited assertions.",
                    "Surface contradictions between sources instead of hiding them.",
                    "Deliver a scoped report with methods, evidence, and uncertainty — not a link dump.",
                ]
            )
            pitfalls.append("Treating search snippets as verified facts without opening sources.")
            constraints.append("Verify load-bearing claims against primary evidence before synthesizing.")

        elif re.search(r"\b(browser|login|scrap|crawl|form|web flow|website)\b", text):
            priorities.extend([PriorityDomain.CORRECTNESS, PriorityDomain.SECURITY, PriorityDomain.REVERSIBILITY])
            unstated_expectations.extend(
                [
                    "Use an isolated browser context; never reuse ambient login sessions by default.",
                    "Verify observed state after each interaction; recover or abort on unexpected pages.",
                    "Leave no side effects (forms, carts, accounts) unless explicitly requested.",
                ]
            )
            pitfalls.append("Submitting forms or mutating accounts during read-only investigation.")
            constraints.append("Require explicit approval before any state-changing browser action.")

        elif re.search(r"\b(deploy|release|publish|rollout|migration|migrate)\b", text):
            priorities.extend([PriorityDomain.SECURITY, PriorityDomain.REVERSIBILITY, PriorityDomain.TEST_COVERAGE])
            unstated_expectations.extend(
                [
                    "Stage, smoke-test, and gate production rollout; never deploy straight to prod.",
                    "Keep a tested rollback path with the release artifact.",
                    "Record what changed, how it was verified, and how to revert.",
                ]
            )
            pitfalls.append("Deploying without a verified rollback or post-deploy check.")
            constraints.append("Block production rollout until staging verification and approval pass.")

        elif re.search(r"\b(data|csv|sql|etl|dataset|dataframe)\b", text):
            priorities.extend([PriorityDomain.CORRECTNESS, PriorityDomain.EVIDENCE, PriorityDomain.REVERSIBILITY])
            unstated_expectations.extend(
                [
                    "Profile data before transforming; report row counts, nulls, and schema drift.",
                    "Keep transforms reproducible with pinned inputs and versioned outputs.",
                    "Validate outputs against expectations before publishing artifacts.",
                ]
            )
            pitfalls.append("Silently dropping rows or coercing types during transformation.")
            constraints.append("Publish row-count and validation deltas with every data artifact.")

        else:
            priorities.extend([PriorityDomain.CORRECTNESS, PriorityDomain.READABILITY])
            unstated_expectations.extend(
                [
                    "Deliver complete and production-ready code with no placeholder 'TODO' comments.",
                    "Follow established codebase styling, naming conventions, and file structures.",
                ]
            )
            constraints.append("Verify code compiles and passes tests before claiming completion.")

        # Workspace grounding adjusts expectations (previously ignored param).
        if workspace_context and workspace_context.strip():
            snippet = workspace_context.strip()[:240]
            unstated_expectations.append(f"Respect the current workspace layout and conventions ({snippet}...).")
            constraints.append("Scope file writes to the workspace; do not touch unrelated trees.")
        if context_metadata:
            prior = context_metadata.get("prior_tasks") or context_metadata.get("history")
            if prior:
                constraints.append("Reuse prior verified approaches from task history; avoid repeating known failures.")

        # 2. Risk tolerance inference
        if any(w in text for w in ["prod", "production", "critical", "security", "auth", "finance"]):
            risk_tolerance = RiskTolerance.LOW
            if PriorityDomain.SECURITY not in priorities:
                priorities.append(PriorityDomain.SECURITY)
            constraints.append("Zero tolerance for unverified assumptions; run complete regression suites.")
        elif any(w in text for w in ["poc", "prototype", "experiment", "spike", "hack"]):
            risk_tolerance = RiskTolerance.HIGH

        return IntentHypothesis(
            stated_goal=task_description.strip(),
            inferred_intent=f"Engineered fulfillment of '{task_description.strip()[:60]}...' with enterprise safety.",
            risk_tolerance=risk_tolerance,
            top_priorities=priorities,
            unstated_expectations=unstated_expectations,
            pitfalls_to_avoid=pitfalls,
            recommended_constraints=constraints,
            confidence_score=_calibrate_confidence(task_description, workspace_context, context_metadata),
        )
