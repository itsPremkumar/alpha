"""Group presence: who is in the room, and what they are actually doing.

The Details pane used to render every participant as ``unknown``. Two real
reasons, and both were the client's:

1. It read the presence word out of ``GET /threads/{id}/agent-messages/roster``
   and ``GET /company/attendance/roll-call``. The first is a *thread-scoped*
   roster, empty for any room the operator did not open as a thread; the second
   returns ``{"org_id", "roll_call_digest"}`` — a markdown string, with no
   ``agents`` array at all — so `asList` found nothing to map.
2. A membership *name* was treated as a status. "architect" is not a state.

Presence is therefore resolved here, from two independent sources that already
own their half of the truth:

- **BotRegistry** owns lifecycle (``status``) and the last recorded activity
  (``last_active``) for every enrolled bot. A room member is auto-provisioned
  into the registry by ``GroupChatService``, so this is the one roster that
  covers every member of every room.
- **Company attendance** owns live liveness — whether a bot is currently
  executing a task, recorded by pulses agents actually send.

Neither is optional. A member with a registry row but no attendance pulse is
``idle``, not ``offline``; a member with neither is ``unknown``, and an
``unknown`` member renders as a neutral dot rather than a green "online" one.
The liveness threshold is a *display* window, not a health verdict — ``
alpha.bots.health`` owns healthy/stale/stalled/dead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

PresenceState = Literal["online", "busy", "idle", "offline", "unknown"]

#: A bot with no recorded activity inside this window reads as ``idle``.
#: Mirrors ``PRESENCE_WINDOW_SECONDS`` in ``frontend/src/lib/time.ts``; the two
#: must not drift, or a dot and its tooltip would disagree.
PRESENCE_WINDOW_SECONDS = 90

#: Status words the BotRegistry may hold, mapped to a presence state. An
#: unrecognised word maps to ``unknown`` rather than defaulting to online.
_REGISTRY_STATUS: dict[str, PresenceState] = {
    "active": "online",
    "sleeping": "idle",
    "suspended": "offline",
    "archived": "offline",
}

#: Lifecycle statuses that mean the bot is administratively out of service.
_OFFLINE_STATUSES = frozenset({"suspended", "archived"})


def _now() -> float:
    import time

    return time.time()


def _parse_epoch(value: Any) -> float | None:
    """Read a stamp as epoch seconds, tolerating the ISO-8601 shape.

    ``BotProfile.last_active`` is an ISO string and ``alpha.bots.inbox`` writes
    ``time.time()`` floats, so one parser has to accept both. Anything
    unreadable is ``None`` — never the current time, which would turn a missing
    stamp into a fresh heartbeat.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if float(value) > 0 else None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.replace(".", "", 1).isdigit():
        try:
            seconds = float(raw)
        except ValueError:
            return None
        return seconds if seconds > 0 else None
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()


@dataclass
class MemberPresence:
    """One room member's resolved presence.

    Every field is measured, and each carries its own provenance so a client can
    say *why* rather than guessing:

    - ``state`` — the resolved presence word.
    - ``source`` — which system answered (``bot_registry``,
      ``company_attendance``, ``bot_registry+company_attendance``, or
      ``unresolved`` when nothing did).
    - ``activity_at`` — the last recorded activity stamp, or ``None``.
    - ``detail`` — a short human string (current task, or the lifecycle status).
    """

    name: str
    state: PresenceState
    source: str
    activity_at: str | None = None
    detail: str = ""
    role: str | None = None
    display_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "source": self.source,
            "activity_at": self.activity_at,
            "detail": self.detail,
            "role": self.role,
            "display_name": self.display_name,
            "metadata": self.metadata,
        }


def _registry_rows() -> dict[str, dict[str, Any]]:
    """BotRegistry status + last activity for every enrolled bot.

    A registry that cannot be read yields ``{}`` — presence then degrades to
    ``unknown`` for everyone, which is honest, rather than raising and taking
    the whole room listing down with it.
    """
    try:
        from alpha.bots.registry import get_bot_registry

        registry = get_bot_registry()
    except Exception:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    try:
        bots = registry.list_bots()
    except Exception:
        return {}
    for bot in bots:
        try:
            name = (getattr(bot, "name", "") or "").strip().lower()
            if not name:
                continue
            rows[name] = {
                "status": (getattr(bot, "status", "") or "").strip().lower(),
                "last_active": getattr(bot, "last_active", None),
                "role": getattr(bot, "role", None),
                "display_name": getattr(bot, "display_name", None),
                "archived": bool(getattr(bot, "archived_at", None)),
            }
        except Exception:
            continue
    return rows


def _attendance_rows() -> dict[str, dict[str, Any]]:
    """Live company-attendance liveness, keyed by bot name.

    Attendance is optional by design: the ledger is per-organization and only
    knows about agents that actually send pulses, and no organization may be
    enrolled at all. A room member absent from it is *not* offline, and callers
    must not read a missing key that way — every failure here returns ``{}`` so
    the caller falls back to the registry instead of rendering a whole room
    unknown.
    """
    rows: dict[str, dict[str, Any]] = {}
    try:
        from alpha.company.organization import get_autonomous_company_engine

        engine = get_autonomous_company_engine()
    except Exception:
        return rows
    try:
        companies = engine.list_companies()
    except Exception:
        return rows
    for company in companies:
        org_id = getattr(company, "org_id", None)
        if not org_id:
            continue
        try:
            ledger = engine.get_attendance_engine(org_id)
        except Exception:
            continue
        if ledger is None:
            continue
        try:
            heartbeats = ledger.list_heartbeats()
        except Exception:
            continue
        for hb in heartbeats:
            name = (getattr(hb, "bot_name", "") or "").strip().lower()
            if not name or name in rows:
                continue
            status = getattr(hb, "status", None)
            rows[name] = {
                "status": str(getattr(status, "value", status) or "").strip().lower(),
                "active_task_id": getattr(hb, "active_task_id", None),
            }
    return rows


def _resolve(
    name: str,
    registry: dict[str, dict[str, Any]],
    attendance: dict[str, dict[str, Any]],
    now: float,
) -> MemberPresence:
    reg = registry.get(name)
    att = attendance.get(name)

    role = reg.get("role") if reg else None
    display_name = reg.get("display_name") if reg else None
    activity_at = reg.get("last_active") if reg else None

    if reg is None and att is None:
        # The name is in `room.members` but no owning system knows it. That is
        # a real state, and it must render as unknown rather than as offline:
        # "we have no record of this member" and "this member is down" are
        # opposite claims.
        return MemberPresence(
            name=name,
            state="unknown",
            source="unresolved",
            activity_at=None,
            detail="no registry or attendance record",
            role=role,
            display_name=display_name,
        )

    sources = []
    if reg is not None:
        sources.append("bot_registry")
    if att is not None:
        sources.append("company_attendance")
    source = "+".join(sources)

    reg_status = (reg or {}).get("status", "")
    att_status = (att or {}).get("status", "")
    active_task = (att or {}).get("active_task_id")

    # Attendance wins when it has an opinion: it is the only source that knows
    # whether a run is executing right now. The words are exactly
    # `AttendanceStatus` (present/busy/idle/stuck_loop/absent/recovered).
    if att is not None and att_status:
        if att_status in ("present", "busy", "in_progress", "working"):
            detail = f"working on {active_task}" if active_task else ("busy" if att_status == "busy" else "present")
            return MemberPresence(name, "busy", source, activity_at, detail, role, display_name)
        if att_status == "stuck_loop":
            # Not offline: the agent is enrolled and reporting, just not
            # finishing. Flattening this to `offline` would hide a stall.
            return MemberPresence(name, "busy", source, activity_at, "stuck in a loop", role, display_name)
        if att_status == "absent":
            return MemberPresence(name, "offline", source, activity_at, "absent", role, display_name)
        if att_status == "idle":
            return MemberPresence(name, "idle", source, activity_at, "idle", role, display_name)

    if reg is not None:
        if reg.get("archived") or reg_status in _OFFLINE_STATUSES:
            detail = reg_status or "archived"
            return MemberPresence(name, "offline", source, activity_at, detail, role, display_name)
        if reg_status == "sleeping":
            return MemberPresence(name, "idle", source, activity_at, "sleeping", role, display_name)
        # Only a status word we actually know may become an availability claim.
        # `active` is the only lifecycle word that grants one: an unrecognised
        # status with a recent timestamp is still just an unknown status, and
        # reading it as "busy" would invent liveness the server never reported.
        if reg_status == "active":
            stamp = _parse_epoch(activity_at)
            if stamp is not None and (now - stamp) <= PRESENCE_WINDOW_SECONDS:
                return MemberPresence(name, "busy", source, activity_at, "active recently", role, display_name)
            return MemberPresence(name, "idle", source, activity_at, "no recent activity", role, display_name)

    # A registry row exists but carried no status word we recognise.
    return MemberPresence(name, "unknown", source, activity_at, "status not reported", role, display_name)


def resolve_room_presence(
    members: list[str],
    *,
    now: float | None = None,
) -> list[MemberPresence]:
    """Resolve every member of one room, in the room's own member order.

    Order is the room's, not the registry's: the room is the thing the operator
    is looking at, and a sorted list would reorder it on every poll.
    """
    moment = _now() if now is None else now
    # A source that raises must not take the roster down with it. The readers
    # below already swallow their own failures; this guards the boundary too,
    # so a future source cannot turn a presence read into a 500 for the room.
    try:
        registry = _registry_rows()
    except Exception:
        logger.warning("Presence: bot registry read failed; states will be unresolved.", exc_info=True)
        registry = {}
    try:
        attendance = _attendance_rows()
    except Exception:
        logger.warning("Presence: attendance read failed; registry alone will answer.", exc_info=True)
        attendance = {}
    seen: set[str] = set()
    out: list[MemberPresence] = []
    for raw in members:
        name = (raw or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(_resolve(name, registry, attendance, moment))
    return out
