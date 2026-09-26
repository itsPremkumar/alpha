"""Task Handoff Protocol, Succession Planning, and Escalation Engine (Master Inventory #32-#36, #43, #134, #170).

Implements atomic task handoff between autonomous bots, succession fallback routing when workers
stall or retire, and hierarchical escalation to managers.

Every transfer made here is also written to the ONE global handoff ledger
(:mod:`alpha.runtime.escalation`) as ``(from, to, reason, attempt)``, and every
escalation opens a durable, queryable record — so a bot handoff is no longer a
fact that only exists in an in-memory package and an org-event line.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.bots.profile import _now
from alpha.bots.registry import BotProfile, BotRegistry, get_bot_registry

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
    attempt: int = 0,
    max_attempts: int = 0,
    reason: str | None = None,
    record_ledger: bool = True,
    registry: BotRegistry | None = None,
) -> TaskHandoffPackage:
    """Execute a structured task handoff between two bots (Inventory #36).

    The handoff is ALSO appended to the global ledger with its from/to/reason/
    attempt, so a transfer made here is visible in the same ordered log as a
    swarm reassignment or a batch-item escalation. Pass
    ``record_ledger=False`` when the caller records the same transfer itself
    under a different domain (the project handoff store does).
    """
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

    if record_ledger:
        _record_bot_handoff(
            task_id=task_id,
            from_ref=from_key,
            to_ref=to_key,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details={"handoff_id": package.handoff_id, "objective": objective[:500], "artifacts": list(artifacts or [])},
        )
    return package


def _record_bot_handoff(
    *,
    task_id: str,
    from_ref: str,
    to_ref: str,
    reason: str | None,
    attempt: int,
    max_attempts: int,
    details: dict[str, Any] | None = None,
) -> None:
    """Append one bot transfer to the global ledger (fail-soft)."""
    try:
        from alpha.bots.failure_reasons import HANDOFF_REQUESTED, SUCCESSION_FALLBACK
        from alpha.runtime.escalation import DOMAIN_BOT, record_handoff

        record_handoff(
            DOMAIN_BOT,
            task_id,
            from_ref,
            to_ref,
            reason=reason or (SUCCESSION_FALLBACK if from_ref != to_ref else HANDOFF_REQUESTED),
            attempt=attempt,
            max_attempts=max_attempts,
            details=dict(details or {}),
        )
    except Exception:  # noqa: BLE001 - the handoff itself already succeeded
        logger.warning("Failed to record bot handoff %s -> %s in the ledger", from_ref, to_ref, exc_info=True)


def _capability_overlap(source: BotProfile, candidate: BotProfile) -> int:
    """How much of ``source``'s work ``candidate`` can actually take over."""
    wanted = {str(c).strip().lower() for c in (list(source.capabilities) + list(source.responsibilities) + list(source.skills)) if str(c).strip()}
    if not wanted:
        return 0
    have = {str(c).strip().lower() for c in (list(candidate.capabilities) + list(candidate.responsibilities) + list(candidate.skills)) if str(c).strip()}
    return len(wanted & have)


def resolve_successor_by_capability(bot_name: str, registry: BotRegistry | None = None) -> str | None:
    """The live bot that can most plausibly take over ``bot_name``'s work.

    Department used to be the only signal, so a stalled SQL data analyst could
    be handed to a frontend engineer purely because they share a floor. This
    scores the whole roster on the stalled bot's declared capabilities,
    responsibilities and skills, and only falls back to the old department
    chain when nothing scores above zero.
    """
    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    source = reg.get_bot(key)
    if not source:
        return None
    best: BotProfile | None = None
    best_score = 0
    for candidate in reg.list_bots(include_archived=False):
        if candidate.name == source.name or candidate.status not in ("active", "sleeping"):
            continue
        score = _capability_overlap(source, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best.name if best is not None and best_score > 0 else None


def resolve_succession(bot_name: str, registry: BotRegistry | None = None) -> str | None:
    """Determine the rightful succession backup for a stalled or retired bot (Inventory #35).

    Resolution priority:
    1. Explicit `succession_fallback` if configured and active.
    2. The live bot whose capabilities best cover the stalled bot's work
       (:func:`resolve_successor_by_capability`).
    3. Peer bot in the same department.
    4. The bot's manager (`reports_to`).
    5. Top-level Architect or CEO.

    Step 2 is capability-first on purpose: department was a proxy for
    capability, and a proxy silently mis-assigns work. When no candidate
    shares a single declared capability the old department chain is used, so
    a roster with thin capability metadata still resolves exactly as before.
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

    # 2. Best capability match anywhere in the roster
    capable = resolve_successor_by_capability(key, registry=reg)
    if capable:
        return capable

    # 3. Department peer
    peers = reg.get_by_department(bot.department)
    for peer in peers:
        if peer.name != bot.name and peer.status in ("active", "sleeping"):
            return peer.name

    # 4. Manager
    if bot.reports_to:
        mgr = reg.get_bot(bot.reports_to.lower().strip())
        if mgr and mgr.status in ("active", "sleeping"):
            return mgr.name

    # 5. Ultimate fallback to architect or cto
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
    attempt: int = 0,
    max_attempts: int = 0,
    to_human: bool = True,
) -> dict[str, Any]:
    """Escalate a blocked or failed task up the organizational hierarchy (Inventory #134, #170).

    Two audiences, two records, both durable:

    * the org event + the returned dict describe the org chart route (manager,
      manager role) that a human operator sees, and
    * ``to_human=True`` additionally opens a queryable escalation record in the
      global store, so an escalation raised by a bot, a batch, a swarm task or a
      subagent is answerable from one list instead of five.
    """
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

    escalation_id: str | None = None
    escalation_status: str = "logged"
    if to_human:
        try:
            from alpha.bots.failure_reasons import classify_work_failure
            from alpha.runtime.escalation import DOMAIN_BOT, escalate_to_human

            record = escalate_to_human(
                DOMAIN_BOT,
                task_id,
                key,
                reason=classify_work_failure(reason),
                attempt=attempt,
                max_attempts=max_attempts,
                detail=f"bot {key} escalated '{task_id}' to {manager.name}: {reason}"[:2000],
                details={"manager": manager.name, "department": bot.department},
            )
            escalation_id = record.escalation_id if record else None
            escalation_status = record.status if record else "failed_to_record"
        except Exception:  # noqa: BLE001 - the org route already succeeded
            logger.warning("Could not open a durable escalation for bot task %s", task_id, exc_info=True)

    return {
        "task_id": task_id,
        "escalated_by": key,
        "escalated_to": manager.name,
        "manager_role": manager.role,
        "reason": reason,
        "timestamp": _now(),
        # Additive: the durable record this escalation now owns.
        "domain": "bot",
        "escalation_id": escalation_id,
        "escalation_status": escalation_status,
        "attempt": attempt,
        "max_attempts": max_attempts,
    }


def record_bot_failure(
    task_id: str,
    bot_name: str,
    error: str,
    *,
    attempt: int,
    max_attempts: int,
    registry: BotRegistry | None = None,
    reassign: bool = True,
) -> dict[str, Any]:
    """Classify a failed bot attempt, bound it, and move the work on.

    The automatic counterpart to :func:`escalate_task`: retry a transient or
    crashed attempt, hand a capability gap to a capable successor, and escalate
    to a human the moment the attempt ceiling is spent — instead of a bot
    quietly failing the same task forever.
    """
    from alpha.bots.failure_reasons import ACTION_ESCALATE, classify_work_failure
    from alpha.runtime.escalation import DOMAIN_BOT, record_failure

    reg = registry or get_bot_registry()
    key = bot_name.lower().strip()
    reason = classify_work_failure(error)
    successor = resolve_successor_by_capability(key, registry=reg) if reassign else None

    disposition = record_failure(
        DOMAIN_BOT,
        task_id,
        key,
        attempt=attempt,
        max_attempts=max_attempts,
        error=error,
        reason=reason,
        successor=successor,
        detail=f"bot {key} failed '{task_id}': {error}"[:2000],
    )
    decision = disposition.decision
    result: dict[str, Any] = {
        "task_id": task_id,
        "bot": key,
        "reason": decision.reason,
        "reason_class": decision.reason_class,
        "action": decision.action,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "successor": successor if decision.action != ACTION_ESCALATE else None,
        "escalation_id": disposition.escalation.escalation_id if disposition.escalation else None,
        "ledger_seq": disposition.entry.seq if disposition.entry else None,
    }
    if successor and decision.action != ACTION_ESCALATE:
        try:
            execute_handoff(
                task_id,
                key,
                successor,
                f"Take over '{task_id}' after attempt {attempt} failed ({decision.reason})",
                context_summary=error[:2000],
                attempt=attempt,
                max_attempts=max_attempts,
                reason=decision.reason,
                registry=reg,
            )
        except ValueError as exc:
            # No eligible successor: escalate rather than pretend the work moved.
            logger.info("Bot succession for %s unavailable (%s); escalating", task_id, exc)
            result["successor"] = None
    return result
