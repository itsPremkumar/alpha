"""Bot Mode agent-to-agent DM: ``message_agent``.

Lets a bot message a teammate (a profile on this install). Fire-and-forget
and asynchronous, like texting: the target is validated against the live
roster, the attribution prefix is applied server-side (never trusted from
the caller — anti-spoof), delivery lands in the recipient's persistent
inbox, and the call returns immediately with an acknowledgement. No reply
is returned; replies arrive as new inbox messages.

Containment: the DM schema must only be offered inside a bot's own chat
session (see :func:`is_bot_chat_context`); dispatch re-checks the gate so
a forged call returns a structured error instead of delivering.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from agent_workspace.bots.failure_reasons import (
    AGENT_BLOCKED,
    DELIVERY_TIMEOUT,
    UNKNOWN,
    is_valid_agent_name,
)

logger = logging.getLogger(__name__)

MESSAGE_AGENT_TOOL_NAME = "message_agent"
MESSAGE_MAX_CHARS = 16000

DMTargetKind = Literal["local", "peer", "connection"]

_PEER_TARGET_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,63})/([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})$")
_CONNECTION_TARGET_RE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})@([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})$")
_ATTRIBUTION_RE = re.compile(r"^\[DM from [^\]]+\]\s*")


def parse_dm_target(target: str) -> tuple[DMTargetKind, str, str | None]:
    """Split a DM target into (kind, name, peer-or-connection).

    - ``researcher`` -> local profile
    - ``<peer>/<agent>`` -> agent on a registered peer gateway
    - ``<handle>@<connection>`` -> agent on another connected machine
    """
    raw = (target or "").strip()
    peer = _PEER_TARGET_RE.match(raw)
    if peer:
        return "peer", peer.group(2), peer.group(1)
    conn = _CONNECTION_TARGET_RE.match(raw)
    if conn:
        return "connection", conn.group(1), conn.group(2)
    if is_valid_agent_name(raw):
        return "local", raw, None
    raise ValueError(f"Invalid DM target '{target}'. Use a teammate name, '<peer>/<agent>', or '<handle>@<connection>'.")


def apply_attribution(sender: str, body: str) -> str:
    """Prefix the body server-side, stripping any caller-supplied prefix first."""
    clean = _ATTRIBUTION_RE.sub("", body.lstrip())
    return f"[DM from {sender}] {clean}"


def is_bot_chat_context(thread_metadata: dict[str, Any] | None) -> bool:
    """Whether a thread is a bot's own chat session (DM tool allowed).

    Our threads carry ``botName`` when scoped to a specialist (set by the
    chat UI); supervisor/operator threads carry an explicit role flag.
    """
    meta = thread_metadata or {}
    if meta.get("botName"):
        return True
    return str(meta.get("role", "")).lower() in ("supervisor", "operator", "bot")


def build_roster_snippet(profiles: list[dict[str, Any]]) -> str:
    """Teammate roster for system-prompt injection (names + roles only)."""
    lines = ["Teammates you may message with `message_agent` (name — role):"]
    for p in profiles:
        name = str(p.get("name", "?"))
        role = str(p.get("role", "specialist"))
        lines.append(f"- {name} — {role}")
    lines.append("Message one clearly relevant teammate when it genuinely helps the goal; never paste private 1:1 chat content.")
    return "\n".join(lines)


def resolve_runtime_bot_name(runtime: Any | None) -> str | None:
    """Best-effort bot name from a LangGraph runtime; None when absent.

    Never raises and never touches disk — safe on every model-call path.
    """
    try:
        if runtime is None:
            return None
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            for key in ("bot_name", "botName", "bot"):
                value = context.get(key)
                if isinstance(value, str) and is_valid_agent_name(value.strip()):
                    return value.strip().lower()
        config = getattr(runtime, "config", None)
        if isinstance(config, dict):
            configurable = config.get("configurable")
            if isinstance(configurable, dict):
                for key in ("bot_name", "botName"):
                    value = configurable.get(key)
                    if isinstance(value, str) and is_valid_agent_name(value.strip()):
                        return value.strip().lower()
            metadata = config.get("metadata")
            if isinstance(metadata, dict):
                value = metadata.get("botName")
                if isinstance(value, str) and is_valid_agent_name(value.strip()):
                    return value.strip().lower()
    except Exception:
        return None
    return None


def build_bot_roster_reminder(bot_name: str | None, *, registry=None, max_teammates: int = 12) -> str | None:
    """Roster reminder for per-turn injection; None when not a bot chat.

    Framework-owned (registry names+roles only), so it rides the system
    channel next to the date reminder without breaking prompt-cache rules:
    the static system prompt stays identical; only the reminder varies.
    """
    if not bot_name or not is_valid_agent_name(bot_name):
        return None
    try:
        from agent_workspace.bots.registry import get_bot_registry

        reg = registry or get_bot_registry()
        teammates = [{"name": b.name, "role": b.role} for b in reg.list_bots() if b.name.lower() != bot_name.lower() and b.status in ("active", "sleeping")][:max_teammates]
        if not teammates:
            return None
        return build_roster_snippet(teammates)
    except Exception:
        return None


def canonical_bot_chat_id(bot_name: str) -> str:
    """Deterministic canonical Bot Chat thread id for a bot.

    Thread creation is idempotent server-side, so opening this id always
    lands in the same DM-visible conversation for the bot.
    """
    slug = "".join(c if (c.isalnum() or c in ("-", "_")) else "-" for c in bot_name.lower().strip()).strip("-")[:48] or "unknown"
    return f"bot-chat-{slug}"


PROTOCOL_MARKER = "<!-- deerflow:dm-protocol -->"


def messaging_protocol_section(bot_name: str) -> str:
    """The DM protocol block every bot SOUL must keep (custom SOULs included)."""
    return (
        f"{PROTOCOL_MARKER}\n"
        "## Messaging other agents\n"
        "You work alongside named teammates. To message one, call `message_agent` with their roster name and your composed message. "
        "Delivery is fire-and-forget: the call returns an acknowledgement, never their reply — finish your turn and their reply arrives later as a new message. "
        "Paraphrase actionable substance; never paste private 1:1 chat content verbatim."
    )


def ensure_messaging_section(soul: str, bot_name: str) -> str:
    """Append the DM protocol block to a SOUL that lacks it; idempotent.

    Custom SOULs used to silently drop DM capability — this keeps it.
    """
    text = soul or ""
    if PROTOCOL_MARKER in text:
        return text
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}\n{messaging_protocol_section(bot_name)}\n"


def backfill_roster_profiles(*, registry=None) -> list[str]:
    """Add the protocol block to stored profiles missing it. Returns updated names."""
    from agent_workspace.bots.registry import get_bot_registry

    reg = registry or get_bot_registry()
    updated: list[str] = []
    for bot in reg.list_bots():
        if PROTOCOL_MARKER not in (bot.soul or ""):
            reg.update_bot(bot.name, soul=ensure_messaging_section(bot.soul, bot.name), bump_version=False)
            updated.append(bot.name)
    return updated


@dataclass
class DMAck:
    delivery_id: str | None
    target: str
    target_kind: str
    status: str
    reason_code: str = UNKNOWN
    detail: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def send_dm(
    sender: str,
    target: str,
    message: str,
    *,
    thread_metadata: dict[str, Any] | None = None,
    require_bot_chat: bool = True,
    registry=None,
) -> DMAck:
    """Validate, attribute, and deliver a bot-to-bot DM. Never blocks on reply."""
    from agent_workspace.bots.inbox import get_bot_inbox
    from agent_workspace.bots.registry import get_bot_registry

    now = time.time()
    sender_key = (sender or "").lower().strip()
    if not is_valid_agent_name(sender_key):
        return DMAck(None, target, "local", "rejected", AGENT_BLOCKED, f"Invalid sender '{sender}'.", now)
    if require_bot_chat and not is_bot_chat_context(thread_metadata):
        return DMAck(None, target, "local", "rejected", AGENT_BLOCKED, "message_agent is only available inside a bot chat session.", now)
    body = (message or "").strip()
    if not body:
        return DMAck(None, target, "local", "rejected", UNKNOWN, "Message body is empty.", now)
    if len(body) > MESSAGE_MAX_CHARS:
        return DMAck(None, target, "local", "rejected", UNKNOWN, f"Message exceeds {MESSAGE_MAX_CHARS} chars.", now)
    try:
        kind, name, _peer = parse_dm_target(target)
    except ValueError as exc:
        return DMAck(None, target, "local", "rejected", UNKNOWN, str(exc), now)
    if kind != "local":
        return DMAck(None, target, kind, "rejected", DELIVERY_TIMEOUT, "Peer/connection delivery is not configured on this install; message a local teammate.", now)

    reg = registry or get_bot_registry()
    sender_profile = reg.get_bot(sender_key)
    if sender_profile is None:
        return DMAck(None, target, kind, "rejected", AGENT_BLOCKED, f"Sender bot '{sender_key}' is not on the roster.", now)
    recipient = reg.get_bot(name.lower())
    if recipient is None:
        return DMAck(None, target, kind, "rejected", AGENT_BLOCKED, f"Target '{name}' is not on the live roster.", now)
    if recipient.status in ("suspended", "archived"):
        return DMAck(None, target, kind, "rejected", AGENT_BLOCKED, f"Target '{name}' is {recipient.status} and cannot receive DMs.", now)

    try:
        msg = get_bot_inbox(recipient.name).deliver(sender_key, apply_attribution(sender_key, body))
    except ValueError as exc:
        return DMAck(None, target, kind, "rejected", UNKNOWN, str(exc), now)

    try:
        from agent_workspace.bots.events import log_org_event

        log_org_event(event_type="bot_dm", actor=sender_key, target=recipient.name, details={"delivery_id": msg.delivery_id})
    except Exception:
        logger.debug("DM event logging failed", exc_info=True)
    return DMAck(msg.delivery_id, target, kind, "delivered", UNKNOWN, "Delivered to inbox; reply arrives as a new message.", now)


def message_agent_tool_schema() -> dict[str, Any]:
    """OpenAI-format schema for ``message_agent`` (inject into bot chats only)."""
    return {
        "type": "function",
        "function": {
            "name": MESSAGE_AGENT_TOOL_NAME,
            "description": (
                "Send a message to ANOTHER agent (teammate). FIRE-AND-FORGET and asynchronous, like texting: "
                "validates the target against the live roster, delivers into that agent's inbox with your "
                "attribution prefixed, and returns immediately with a delivery acknowledgement. It does NOT "
                "return their reply — send it, finish your turn, and the reply arrives later as a new message. "
                "Compose the message yourself (lead with the point; paraphrase, never paste private chat verbatim). "
                "Message one clearly relevant teammate when it genuinely helps the goal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Teammate profile name from your roster (e.g. 'researcher')."},
                    "message": {"type": "string", "description": "What you want to say (max 16000 chars)."},
                },
                "required": ["target", "message"],
            },
        },
    }
