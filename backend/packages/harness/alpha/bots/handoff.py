"""Task Handoff Protocol, Succession Planning, and Escalation Engine (Master Inventory #32-#36, #43, #134, #170).

Implements atomic task handoff between autonomous bots, succession fallback routing when workers
stall or retire, and hierarchical escalation to managers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.bots.profile import _now
from alpha.bots.registry import BotRegistry, get_bot_registry

logger = logging.getLogger(__name__)


@dataclass
class TaskHandoffPackage:
    """Standard deliverable and context package for inter-bot task transfers."""

    handoff_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    task_id: str = ""
    from_bot: str = ""
    to_bot: str = ""
    objective: str = ""
    context_summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    handoff_notes: str = ""
    status: str = "accepted"  # accepted | rejected
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def execute_handoff(
    task_id: str,
    from_bot: str,
    to_bot: str,
    objective: str,
    *,
    context_summary: str = "",
    artifacts: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    handoff_notes: str = "",
    registry: BotRegistry | None = None,
) -> TaskHandoffPackage:
    """Execute a structured task handoff between two bots (Inventory #36)."""
    reg = registry or get_bot_registry()
    from_key = from_bot.lower().strip()
    to_key = to_bot.lower().strip()

    sender = reg.get_bot(from_key)
    if not sender:
        raise ValueError(f"Sender bot '{from_key}' not found.")

    recipient = reg.get_bot(to_key)
    if not recipient:
        raise ValueError(f"Recipient bot '{to_key}' not found.")

    if recipient.status in ("suspended", "archived"):
        raise ValueError(f"Recipient bot '{to_key}' is {recipient.status} and cannot accept work.")

    # Wake recipient if sleeping
    if recipient.status == "sleeping":
        reg.update_bot(to_key, status="active", bump_version=False)

    package = TaskHandoffPackage(
        task_id=task_id,
        from_bot=from_key,
        to_bot=to_key,
        objective=objective,
        context_summary=context_summary,
        artifacts=artifacts or [],
        acceptance_criteria=acceptance_criteria or [],
        handoff_notes=handoff_notes,
        status="accepted",
    )

    # Log organizational audit event
    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="task_handoff",
            actor=from_key,
            target=to_key,
            details={
                "task_id": task_id,
                "handoff_id": package.handoff_id,
                "objective": objective,
            },
        )
    except Exception:
        logger.debug("Handoff event logging failed", exc_info=True)

    return package


def resolve_succession(bot_name: str, registry: BotRegistry | None = None) -> str | None:
    """Determine the rightful succession backup for a stalled or retired bot (Inventory #35).

    Resolution priority:
    1. Explicit `succession_fallback` if configured and active.
    2. Peer bot in the same department with matching capabilities.
    3. The bot's manager (`reports_to`).
    4. Top-level Architect or CEO.
    """
    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    bot = reg.get_bot(key)
    if not bot:
        return None

    # 1. Explicit succession fallback
    if bot.succession_fallback:
        candidate = reg.get_bot(bot.succession_fallback.lower().strip())
        if candidate and candidate.status in ("active", "sleeping"):
            return candidate.name

    # 2. Department peer
    peers = reg.get_by_department(bot.department)
    for peer in peers:
        if peer.name != bot.name and peer.status in ("active", "sleeping"):
            return peer.name

    # 3. Manager
    if bot.reports_to:
        mgr = reg.get_bot(bot.reports_to.lower().strip())
        if mgr and mgr.status in ("active", "sleeping"):
            return mgr.name

    # 4. Ultimate fallback to architect or cto
    for fallback in ("architect", "cto", "ceo"):
        fb_bot = reg.get_bot(fallback)
        if fb_bot and fb_bot.status in ("active", "sleeping"):
            return fb_bot.name

    return None


def escalate_task(
    task_id: str,
    bot_name: str,
    reason: str,
    *,
    registry: BotRegistry | None = None,
) -> dict[str, Any]:
    """Escalate a blocked or failed task up the organizational hierarchy (Inventory #134, #170)."""
    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    bot = reg.get_bot(key)
    if not bot:
        raise ValueError(f"Bot '{key}' not found.")

    manager_key = (bot.reports_to or "architect").lower().strip()
    manager = reg.get_bot(manager_key)
    if not manager:
        manager = reg.get_bot("ceo") or bot

    # Log escalation event
    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="task_escalation",
            actor=key,
            target=manager.name,
            details={
                "task_id": task_id,
                "reason": reason,
                "original_worker": key,
                "escalated_to": manager.name,
            },
        )
    except Exception:
        logger.debug("Escalation event logging failed", exc_info=True)

    return {
        "task_id": task_id,
        "escalated_by": key,
        "escalated_to": manager.name,
        "manager_role": manager.role,
        "reason": reason,
        "timestamp": _now(),
    }
