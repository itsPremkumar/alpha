"""Goal Integrity and Scope Creep Engine.

Monitors plan execution and candidate subtasks against top-level mission goals
to prevent goal drift, unwarranted scope creep, overengineering, and wasted compute.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Stopwords to filter out during semantic token overlap calculations
_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "when",
    "at",
    "by",
    "for",
    "with",
    "about",
    "against",
    "between",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "to",
    "from",
    "up",
    "down",
    "in",
    "out",
    "on",
    "off",
    "over",
    "under",
    "again",
    "further",
    "once",
    "here",
    "there",
    "all",
    "any",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "can",
    "will",
    "just",
    "should",
    "now",
    "this",
    "that",
    "these",
    "those",
    "i",
    "we",
    "our",
    "you",
    "your",
    "it",
}

_TRIVIAL_VERBS = {"format", "fix typo", "rename", "lowercase", "sort", "clean", "comment"}
_COMPLEX_INFRA_TERMS = {
    "kubernetes",
    "k8s",
    "kafka",
    "microservices",
    "distributed",
    "cluster",
    "redis cluster",
    "shard",
    "consensus",
    "raft",
    "paxos",
    "event streaming",
}
_STANDARD_DEV_TERMS = {
    "test",
    "tests",
    "unit",
    "mock",
    "ui",
    "style",
    "css",
    "component",
    "index",
    "query",
    "endpoint",
    "api",
    "database",
    "db",
    "lint",
    "docs",
    "review",
    "refactor",
    "build",
    "deploy",
    "setup",
    "create",
    "update",
    "patch",
    "fix",
    "validate",
    "card",
    "table",
    "pricing",
    "responsive",
    "login",
    "auth",
}


class GoalIntegrityReport(BaseModel):
    mission_goal: str
    audited_subtasks_count: int = 0
    drift_score: float = Field(default=0.0, ge=0.0, le=1.0, description="0.0 = perfect alignment, 1.0 = total divergence")
    is_aligned: bool = True
    scope_creep_detected: bool = False
    overengineering_detected: bool = False
    flagged_tasks: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)


class GoalIntegrityEngine:
    """Audits plans and task execution for goal drift, scope creep, and overengineering."""

    @classmethod
    def _tokenize(cls, text: str) -> set[str]:
        words = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", text.lower())
        return {w for w in words if w not in _STOPWORDS}

    @classmethod
    def audit_plan(
        cls,
        mission_goal: str,
        subtasks: list[dict[str, Any]] | list[str],
    ) -> GoalIntegrityReport:
        """Audits a list of proposed or active subtasks against the core mission goal."""
        goal_tokens = cls._tokenize(mission_goal)
        goal_lower = mission_goal.lower()

        # Normalize subtasks to list of strings
        normalized_tasks: list[tuple[str, str]] = []  # (id_or_idx, description)
        for idx, item in enumerate(subtasks):
            if isinstance(item, dict):
                tid = str(item.get("task_id", f"task-{idx}"))
                desc = str(item.get("description", item.get("objective", "")))
                normalized_tasks.append((tid, desc))
            else:
                normalized_tasks.append((f"task-{idx}", str(item)))

        if not normalized_tasks:
            return GoalIntegrityReport(
                mission_goal=mission_goal,
                audited_subtasks_count=0,
                drift_score=0.0,
                is_aligned=True,
                findings=["No subtasks provided to audit."],
            )

        flagged_tasks: list[dict[str, Any]] = []
        findings: list[str] = []
        recommendations: list[str] = []

        total_drift = 0.0
        scope_creep_found = False
        overengineering_found = False

        # 1. Check for Overengineering heuristic
        # If the goal is trivial/short and more than 3 subtasks or heavy enterprise infra introduced
        is_trivial_goal = any(v in goal_lower for v in _TRIVIAL_VERBS) or len(goal_tokens) <= 3
        if is_trivial_goal and len(normalized_tasks) > 3:
            overengineering_found = True
            findings.append(f"Overengineering detected: Simple goal '{mission_goal}' was fragmented into {len(normalized_tasks)} subtasks.")
            recommendations.append("Consolidate subtasks into a single direct execution action.")

        # 2. Per-task semantic overlap and scope creep checks
        for tid, desc in normalized_tasks:
            desc_tokens = cls._tokenize(desc)
            desc_lower = desc.lower()

            # Check unrequested complex infrastructure scope creep
            unsolicited_infra = [term for term in _COMPLEX_INFRA_TERMS if term in desc_lower and term not in goal_lower]
            if unsolicited_infra:
                scope_creep_found = True
                flagged_tasks.append(
                    {
                        "task_id": tid,
                        "description": desc,
                        "reason": f"Introduced unrequested complex infrastructure: {', '.join(unsolicited_infra)}",
                    }
                )
                findings.append(f"Task '{tid}' introduces scope creep: {', '.join(unsolicited_infra)}.")

            # Calculate semantic alignment against goal and dev context
            direct_overlap = len(goal_tokens & desc_tokens)
            dev_context_overlap = len(desc_tokens & _STANDARD_DEV_TERMS)

            if unsolicited_infra:
                task_drift = 1.0
            elif direct_overlap >= 1:
                task_drift = 0.15
            elif dev_context_overlap >= 1:
                task_drift = 0.30
            else:
                task_drift = 0.85

            total_drift += task_drift

            # If task has zero overlap with goal or dev context and is long, flag as potential drift
            if direct_overlap == 0 and dev_context_overlap == 0 and len(desc_tokens) > 3:
                flagged_tasks.append(
                    {
                        "task_id": tid,
                        "description": desc,
                        "reason": "Zero semantic overlap with original mission goal.",
                    }
                )

        avg_drift = min(1.0, round(total_drift / len(normalized_tasks), 2))

        is_aligned = avg_drift < 0.65 and not scope_creep_found

        if scope_creep_found:
            recommendations.append("Prune unrequested architectural additions; restrict scope to explicit user requirements.")
        if avg_drift >= 0.65:
            findings.append(f"High goal drift detected (score={avg_drift}). Work has diverged from core objective.")
            recommendations.append("Realign task queue to focus strictly on the top-level deliverables.")

        return GoalIntegrityReport(
            mission_goal=mission_goal,
            audited_subtasks_count=len(normalized_tasks),
            drift_score=avg_drift,
            is_aligned=is_aligned,
            scope_creep_detected=scope_creep_found,
            overengineering_detected=overengineering_found,
            flagged_tasks=flagged_tasks,
            findings=findings if findings else ["All subtasks closely align with the mission goal."],
            recommendations=recommendations,
        )
