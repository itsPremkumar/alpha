"""Agent Performance and Dynamic Reputation Engine (Master Inventory #51-#52, #149-#151).

Tracks run statistics (completed, failed, duration, SLA compliance) and dynamically computes
peer/work reputation scores (0.0 - 1.0) for every autonomous bot.
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.bots.health import get_health_monitor
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry, get_bot_registry

logger = logging.getLogger(__name__)


def _get_reputation_tier(score: float) -> str:
    if score >= 0.90:
        return "Elite Specialist"
    elif score >= 0.75:
        return "Proven Teammate"
    elif score >= 0.50:
        return "Standard Contributor"
    return "Under Review"


def record_task_outcome(
    bot_name: str,
    *,
    success: bool,
    duration_sec: float = 0.0,
    quality_score: float | None = None,
    task_id: str | None = None,
    registry: BotRegistry | None = None,
) -> BotProfile | None:
    """Record task completion or failure and update bot reputation dynamically (Inventory #51, #52)."""
    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    bot = reg.get_bot(key)
    if not bot:
        return None

    stats = dict(bot.task_stats or {})
    total = stats.get("total_runs", 0) + 1
    completed = stats.get("completed", 0) + (1 if success else 0)
    failed = stats.get("failed", 0) + (0 if success else 1)

    # Rolling average calculation
    old_avg = float(stats.get("avg_duration_sec", 0.0))
    new_avg = round(((old_avg * (total - 1)) + duration_sec) / total, 2)

    updated_stats = {
        "completed": completed,
        "failed": failed,
        "total_runs": total,
        "avg_duration_sec": new_avg,
    }

    # Dynamic reputation adjustment
    current_rep = bot.reputation_score if bot.reputation_score is not None else 1.0
    if success:
        boost = 0.02
        if quality_score is not None:
            boost = 0.01 + (0.03 * max(0.0, min(1.0, quality_score)))
        new_rep = min(1.0, round(current_rep + boost, 3))
    else:
        penalty = 0.05
        new_rep = max(0.0, round(current_rep - penalty, 3))

    # Clear active task lease
    get_health_monitor().clear_task_lease(key)

    # Persist updates
    updated_bot = reg.update_bot(
        key,
        reputation_score=new_rep,
        task_stats=updated_stats,
        bump_version=False,
    )

    # Log event
    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="task_completed" if success else "task_failed",
            actor=key,
            target=task_id,
            details={
                "success": success,
                "duration_sec": duration_sec,
                "reputation_score": new_rep,
                "quality_score": quality_score,
            },
        )
    except Exception:
        logger.debug("Task outcome event logging failed", exc_info=True)

    return updated_bot


def get_bot_performance(
    bot_name: str,
    *,
    registry: BotRegistry | None = None,
) -> dict[str, Any]:
    """Retrieve detailed execution stats and reputation breakdown for a bot."""
    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    bot = reg.get_bot(key)
    if not bot:
        raise ValueError(f"Bot '{key}' not found.")

    stats = bot.task_stats or {"completed": 0, "failed": 0, "total_runs": 0, "avg_duration_sec": 0.0}
    total = stats.get("total_runs", 0)
    completed = stats.get("completed", 0)
    success_rate = round((completed / total * 100), 1) if total > 0 else 100.0
    rep = bot.reputation_score if bot.reputation_score is not None else 1.0

    return {
        "bot_name": bot.name,
        "display_name": bot.display_name,
        "role": bot.role,
        "department": bot.department,
        "reputation_score": rep,
        "reputation_tier": _get_reputation_tier(rep),
        "success_rate_percent": success_rate,
        "total_runs": total,
        "completed_runs": completed,
        "failed_runs": stats.get("failed", 0),
        "avg_duration_seconds": stats.get("avg_duration_sec", 0.0),
    }
