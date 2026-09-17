"""Workload-aware agent selection: capability -> availability -> load -> reputation -> cost.

Reads live data from the bot registry, health monitor, and performance
ledger. Historical reputation decays so past scores inform but never freeze
routing.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

REPUTATION_HALF_LIFE_DAYS = 30.0


def decayed_reputation(raw_score: float, last_active_iso: str | None) -> float:
    """Apply time decay so stale glory fades toward the 0.75 prior."""
    if not last_active_iso:
        return raw_score
    try:
        from datetime import UTC, datetime

        last = datetime.fromisoformat(last_active_iso)
        age_days = max(0.0, (datetime.now(UTC) - last).total_seconds() / 86400.0)
    except Exception:
        return raw_score
    factor = 0.5 ** (age_days / REPUTATION_HALF_LIFE_DAYS)
    return round(0.75 + (raw_score - 0.75) * factor, 3)


@dataclass
class Candidate:
    bot_name: str
    capability_match: float = 0.0
    available: bool = False
    load: float = 0.0
    reputation: float = 0.75
    cost_class: str = "standard"
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _capability_match(advertised: list[str], required: list[str]) -> float:
    if not required:
        return 1.0
    have = {c.lower() for c in (advertised or [])}
    hit = sum(1 for r in required if r.lower() in have or any(r.lower() in h for h in have))
    return round(hit / len(required), 3)


def rank_candidates(
    required_capabilities: list[str],
    *,
    exclude: set[str] | None = None,
    limit: int = 5,
    registry=None,
    memberships: list | None = None,
) -> list[Candidate]:
    """Rank bots without side effects. Pure selection; caller assigns."""
    from agent_workspace.bots.health import get_health_monitor
    from agent_workspace.bots.registry import get_bot_registry

    reg = registry or get_bot_registry()
    monitor = get_health_monitor()
    excluded = {e.lower() for e in (exclude or set())}
    busy_by_bot: dict[str, int] = {}
    for m in memberships or []:
        if getattr(m, "current_task_id", None):
            busy_by_bot[m.bot_name.lower()] = busy_by_bot.get(m.bot_name.lower(), 0) + 1

    bots = reg.list_bots() if hasattr(reg, "list_bots") else []
    out: list[Candidate] = []
    for bot in bots:
        name = bot.name.lower()
        if name in excluded or bot.status in ("suspended", "archived"):
            continue
        live = monitor.evaluate_liveness(bot)
        available = bool(live.get("is_responsive")) and bot.status in ("active", "sleeping")
        load = min(1.0, (busy_by_bot.get(name, 0) + (1 if live.get("active_task_id") else 0)) / 4.0)
        match = _capability_match(list(bot.capabilities or []) + list(bot.toolsets or []), required_capabilities)
        if match <= 0:
            continue
        rep = decayed_reputation(float(bot.reputation_score or 0.75), bot.last_active)
        score = round(0.45 * match + 0.25 * (1.0 if available else 0.0) + 0.15 * (1.0 - load) + 0.15 * rep, 3)
        out.append(Candidate(bot_name=name, capability_match=match, available=available, load=load, reputation=rep, score=score))
    out.sort(key=lambda c: (c.available, c.score), reverse=True)
    return out[:limit]


def select_agent(required_capabilities: list[str], *, exclude: set[str] | None = None, memberships: list | None = None, registry=None) -> Candidate | None:
    ranked = rank_candidates(required_capabilities, exclude=exclude, memberships=memberships, registry=registry)
    for cand in ranked:
        if cand.available:
            cand.reasons.append("highest available score by capability/load/reputation")
            return cand
    if ranked:
        ranked[0].reasons.append("no responsive agent; best-effort fallback")
        return ranked[0]
    return None


def record_routing_feedback(bot_name: str, *, success: bool, duration_sec: float = 0.0, quality_score: float | None = None) -> None:
    """Close the loop: task outcomes update reputation for future routing."""
    try:
        from agent_workspace.bots.performance import record_task_outcome

        record_task_outcome(bot_name, success=success, duration_sec=duration_sec, quality_score=quality_score)
    except Exception:
        logger.debug("Routing feedback failed for %s", bot_name, exc_info=True)
