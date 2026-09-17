"""Continuous Work Discovery and Autonomous Backlog Prioritization Engine."""

from __future__ import annotations

import logging
from typing import Any

from agent_workspace.company.models import (
    DiscoveredWorkItem,
    WorkCategory,
    WorkPriority,
)

logger = logging.getLogger(__name__)

# Category to Department routing map
_CATEGORY_DEPT_MAP = {
    WorkCategory.BUG: "engineering",
    WorkCategory.SECURITY: "security",
    WorkCategory.MAINTENANCE: "sre",
    WorkCategory.OPTIMIZATION: "engineering",
    WorkCategory.RESEARCH: "research",
    WorkCategory.FEATURE: "product",
    WorkCategory.TECHNICAL_DEBT: "engineering",
    WorkCategory.COST_REDUCTION: "operations",
    WorkCategory.OPPORTUNITY: "executive",
}


class ContinuousWorkDiscoveryEngine:
    """Discovers, scores, and prioritizes new engineering and organizational work."""

    @classmethod
    def calculate_priority_score(
        cls,
        alignment: float = 0.8,
        impact: float = 0.7,
        urgency: float = 0.6,
        value: float = 0.7,
        risk: float = 0.2,
        cost: float = 0.2,
    ) -> float:
        """Computes multi-factor priority score (0.0 to 1.0)."""
        score = (0.25 * alignment) + (0.20 * impact) + (0.20 * urgency) + (0.15 * value) - (0.10 * risk) - (0.10 * cost)
        return round(max(0.0, min(1.0, score)), 3)

    @classmethod
    def score_to_priority(cls, score: float) -> WorkPriority:
        if score >= 0.70:
            return WorkPriority.DO_NOW
        elif score >= 0.40:
            return WorkPriority.QUEUE
        elif score >= 0.20:
            return WorkPriority.MONITOR
        else:
            return WorkPriority.IGNORE

    def discover_from_sources(
        self,
        signals: list[dict[str, Any]],
        mission_keywords: set[str] | None = None,
    ) -> tuple[list[DiscoveredWorkItem], bool]:
        """Discovers new work items from telemetry, logs, git signals, or market inputs.

        Returns (items, should_workers_sleep).
        If no actionable work is discovered, should_workers_sleep is True.
        """
        discovered_items: list[DiscoveredWorkItem] = []
        mission_keys = mission_keywords or {"ai", "software", "product", "security", "customer"}

        for sig in signals:
            title = sig.get("title", "Discovered Task")
            cat_str = sig.get("category", "maintenance").lower()
            try:
                category = WorkCategory(cat_str)
            except ValueError:
                category = WorkCategory.MAINTENANCE

            desc = sig.get("description", "")
            alignment = 0.9 if any(k in f"{title} {desc} {cat_str}".lower() for k in mission_keys) else 0.5
            impact = float(sig.get("impact", 0.7))
            urgency = float(sig.get("urgency", 0.5))
            value = float(sig.get("value", 0.6))
            risk = float(sig.get("risk", 0.2))
            cost = float(sig.get("cost", 0.2))

            score = self.calculate_priority_score(
                alignment=alignment,
                impact=impact,
                urgency=urgency,
                value=value,
                risk=risk,
                cost=cost,
            )
            priority = self.score_to_priority(score)

            # Filter out ignored low-value work
            if priority != WorkPriority.IGNORE:
                target_dept = _CATEGORY_DEPT_MAP.get(category, "engineering")
                item = DiscoveredWorkItem(
                    title=title,
                    category=category,
                    description=desc,
                    priority=priority,
                    score=score,
                    target_department=target_dept,
                )
                discovered_items.append(item)

        # Sort items by priority score descending
        discovered_items.sort(key=lambda i: i.score, reverse=True)

        # Zero Wasted Compute Rule: If no items need immediate action or queueing, sleep
        actionable = [i for i in discovered_items if i.priority in (WorkPriority.DO_NOW, WorkPriority.QUEUE)]
        should_sleep = len(actionable) == 0

        return discovered_items, should_sleep
