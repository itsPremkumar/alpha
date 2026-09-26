"""Autonomous Work Discovery, Capability Matching, and Task Claiming Engine (Master Inventory #44-#50, #128-#131, #166-#167).

Empowers bots to continuously discover work matching their declared capabilities,
score compatibility, and claim tasks under time-bound leases.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from alpha.bots.health import get_health_monitor
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry, get_bot_registry

logger = logging.getLogger(__name__)


def _compute_match_score(
    bot: BotProfile,
    task_text: str,
    required_skills: list[str] | None,
    required_department: str | None,
) -> tuple[float, list[str]]:
    """Compute a 0.0 to 1.0 compatibility score between a bot and a task."""
    tokens = set(re.findall(r"[a-z0-9_]+", task_text.lower()))
    reasons = []
    score = 0.0

    # 1. Department match
    if required_department:
        if bot.department.lower() == required_department.lower():
            score += 0.20
            reasons.append(f"Department match ({bot.department})")
        else:
            # Department mismatch penalty
            score -= 0.10
    else:
        score += 0.05

    # 2. Role & Responsibilities keyword overlap
    domain_text = f"{bot.role} {' '.join(bot.responsibilities)} {' '.join(bot.capabilities)}".lower()
    domain_tokens = set(re.findall(r"[a-z0-9_]+", domain_text))
    overlap = tokens.intersection(domain_tokens)
    if overlap:
        # Scale keyword match
        kw_score = min(len(overlap) * 0.08, 0.40)
        score += kw_score
        reasons.append(f"Keyword match: {', '.join(sorted(list(overlap))[:4])}")

    # 3. Required skills / toolsets overlap
    bot_skills_set = {s.lower() for s in bot.skills}
    bot_caps_set = {c.lower() for c in bot.capabilities}
    if required_skills:
        req_set = {s.lower() for s in required_skills}
        matched_skills = req_set.intersection(bot_skills_set.union(bot_caps_set))
        if matched_skills:
            skill_score = min(len(matched_skills) / len(required_skills) * 0.25, 0.25)
            score += skill_score
            reasons.append(f"Skills matched: {', '.join(matched_skills)}")
    else:
        score += 0.10

    # 4. Reputation weighting.  ``None`` means unverified, not perfect;
    # preserve a real zero score as zero rather than upgrading it.
    reputation = bot.reputation_score if bot.reputation_score is not None else 0.5
    score += max(0.0, min(1.0, float(reputation))) * 0.15

    # Bound in 0.05 - 0.99
    final_score = round(max(0.05, min(0.99, score)), 2)
    return final_score, reasons


def match_bot_for_task(
    task_description: str,
    *,
    required_skills: list[str] | None = None,
    required_department: str | None = None,
    limit: int = 5,
    registry: BotRegistry | None = None,
) -> list[dict[str, Any]]:
    """Rank available bots by capability and suitability for a specific task (Inventory #50, #167)."""
    reg = registry or get_bot_registry()
    candidates = [b for b in reg.list_bots() if b.status in ("active", "sleeping")]

    ranked = []
    for bot in candidates:
        score, reasons = _compute_match_score(
            bot,
            task_description,
            required_skills,
            required_department,
        )
        ranked.append(
            {
                "bot_name": bot.name,
                "display_name": bot.display_name,
                "role": bot.role,
                "department": bot.department,
                "avatar": bot.avatar,
                "status": bot.status,
                "reputation_score": bot.reputation_score,
                "match_score": score,
                "reasons": reasons,
            }
        )

    # Sort descending by match_score
    ranked.sort(key=lambda x: x["match_score"], reverse=True)
    return ranked[:limit]


def claim_task(
    task_id: str,
    bot_name: str,
    *,
    lease_seconds: int = 300,
    registry: BotRegistry | None = None,
) -> dict[str, Any]:
    """Autonomous bot claims a task and acquires a time-bound lease (Inventory #29, #44)."""
    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    bot = reg.get_bot(key)
    if not bot:
        raise ValueError(f"Bot '{key}' not found.")
    # Lifecycle gate, FIRST. Retirement is a TWO-PHASE transition: a draining or
    # retired profile must not be handed NEW work, or retirement would be
    # cosmetic. Going through the registry's authorized read means the claim also
    # re-validates the profile against the current authority ceiling, so a
    # tightened ceiling stops dispatch to an over-privileged profile at its next
    # claim rather than leaving it quietly working.
    #
    # This runs before the legacy status check below so the ceiling-aware gate is
    # the single authority on whether work may be handed out; the legacy check
    # stays as a defence for statuses the gate does not know about.
    authorized = reg.authorized_bot(key)
    if authorized is None:
        raise ValueError(
            f"Bot '{key}' is {bot.status!r} and may not receive new work "
            f"(retired, draining, disabled, or demoted below the authority ceiling)."
        )
    bot = authorized
    if bot.status in ("suspended", "archived"):
        raise ValueError(f"Bot '{key}' is {bot.status} and cannot claim tasks.")

    # Wake bot if sleeping
    if bot.status == "sleeping":
        reg.update_bot(key, status="active", bump_version=False)

    monitor = get_health_monitor()
    hb = monitor.record_heartbeat(
        key,
        task_id=task_id,
        lease_seconds=lease_seconds,
        details={"claimed_task": task_id},
    )

    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="task_claimed",
            actor=key,
            target=task_id,
            details={"lease_expires_at": hb.lease_expires_at},
        )
    except Exception:
        logger.debug("Task claim event logging failed", exc_info=True)

    return {
        "task_id": task_id,
        "claimed_by": key,
        "lease_acquired_at": hb.timestamp,
        "lease_expires_at": hb.lease_expires_at,
        "lease_seconds": lease_seconds,
    }
