"""The ``message_agent`` tool: bot-to-bot direct messaging.

``alpha.bots.dm`` owns the policy (roster validation, attribution, ESTOP, Mods
gate, inbox delivery).  This module is only the LangChain binding that exposes
that policy to a model, so there is exactly one implementation of a DM.

Before this module existed the tool was *described* in three places — the lead
prompt, every bot SOUL, and ``GET /api/bots/{name}/dm-schema`` — but was never
registered, so a bot that followed its own instructions called a tool that did
not exist.  Registering it here makes those three surfaces true.

Containment: ``send_dm(require_bot_chat=True)`` re-checks the bot-chat gate on
dispatch, so a call from an ordinary operator thread returns a structured
refusal instead of delivering.  The gate is enforced in policy, not here.
"""

# NOTE: no ``from __future__ import annotations`` — under PEP 563 the
# ``runtime: Runtime`` annotation becomes the *string* "Runtime", and LangChain's
# injected-argument detection inspects the annotation object, so a string never
# matches and ``runtime`` would not be injected (see ``alpha.tools.types.Runtime``).

import json

from langchain.tools import tool

from alpha.bots.dm import (
    MESSAGE_AGENT_TOOL_NAME,
    resolve_runtime_bot_name,
    send_dm,
)
from alpha.tools.types import Runtime

#: Statuses that mean the message was accepted for delivery.  A peer DM can be
#: ``queued`` rather than ``delivered``; it still left this installation, so a
#: reply-to acknowledgement is legitimate.  ``rejected``/``deferred`` are not.
_ACCEPTED_STATUSES = frozenset({"delivered", "queued"})


def _runtime_context(runtime: Runtime | None) -> dict:
    if runtime is None:
        return {}
    context = getattr(runtime, "context", None)
    return context if isinstance(context, dict) else {}


def _thread_metadata(runtime: Runtime | None, sender: str | None) -> dict:
    """Build the metadata ``is_bot_chat_context`` reads.

    A resolved sender identity is itself the proof that this is a bot session:
    only a bot-scoped run carries ``bot_name`` in its context.  Carrying
    ``botName`` forward keeps the policy check meaningful rather than passing a
    hand-built flag that could be forged.
    """
    context = _runtime_context(runtime)
    metadata: dict = {}
    thread_id = context.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        metadata["thread_id"] = thread_id
    if sender:
        metadata["botName"] = sender
    role = context.get("role")
    if isinstance(role, str) and role:
        metadata["role"] = role
    return metadata


@tool(MESSAGE_AGENT_TOOL_NAME, parse_docstring=True)
def message_agent_tool(
    runtime: Runtime,
    target: str,
    message: str,
    reply_to: str = "",
) -> str:
    """Send a message to ANOTHER agent (a teammate on this installation).

    Fire-and-forget and asynchronous, like texting. The target is validated
    against the live roster, your attribution is prefixed server-side, and the
    message lands in that agent's persistent inbox. The call returns a delivery
    acknowledgement immediately — it does NOT return their reply. Finish your
    turn; their reply arrives later as a new inbox message.

    Compose the message yourself: lead with the point, paraphrase actionable
    substance, never paste private one-to-one chat content verbatim.

    Args:
        target: Teammate profile name from your roster (e.g. 'researcher').
            Also accepts '<peer>/<agent>' for a paired installation and
            '<handle>@<connection>' for another connected machine.
        message: What you want to say (max 16000 characters).
        reply_to: Optional delivery_id of an inbox message you are answering.
            When the send succeeds, that original message is acknowledged.
    """

    sender = resolve_runtime_bot_name(runtime)
    if not sender:
        return json.dumps(
            {
                "status": "rejected",
                "reason_code": "not_a_bot_chat",
                "detail": (
                    "message_agent is only available inside a bot chat session; "
                    "this run has no bot identity."
                ),
            }
        )

    ack = send_dm(
        sender,
        target,
        message,
        thread_metadata=_thread_metadata(runtime, sender),
        require_bot_chat=True,
    )
    payload = ack.to_dict()

    reply_to = (reply_to or "").strip()
    if reply_to:
        # Acknowledgement is a side effect of a successful send, so it happens
        # only after delivery was accepted and never changes the send outcome.
        payload["replied_to"] = reply_to
        payload["reply_acknowledged"] = False
        if ack.status in _ACCEPTED_STATUSES:
            try:
                from alpha.bots.inbox import get_bot_inbox

                original = get_bot_inbox(sender).ack(reply_to)
                payload["reply_acknowledged"] = original is not None
            except Exception as exc:  # pragma: no cover - defensive
                payload["reply_ack_detail"] = f"ack failed: {exc}"

    return json.dumps(payload)
