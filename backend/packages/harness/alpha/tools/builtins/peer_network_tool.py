"""Model-facing Alpha peer-network control tool.

The tool is intentionally capability-oriented: agents can discover public
capability cards and send bounded messages, but they cannot read pairing codes,
raw credentials, private endpoints, or another user's local UI state.
"""

# Deliberately NO `from __future__ import annotations` here: this module
# declares an injected `runtime: Runtime` parameter. Under PEP 563 the
# annotation collapses to the string "Runtime", LangChain's injected-argument
# detection stops matching it, and `runtime` is exposed to the model as a
# REQUIRED schema field it must supply. `requires-python >=3.12` evaluates
# `X | None` natively, so the import is unnecessary anyway. Guarded by
# test_tool_name_references.py::test_injected_runtime_annotation_is_not_hidden_by_pep563.

import asyncio
import json
import logging

from langchain.tools import tool

from alpha.peer_network.models import ConversationCreateRequest, MessageCreateRequest
from alpha.peer_network.service import get_peer_network_service
from alpha.tools.types import Runtime

logger = logging.getLogger(__name__)


def _public_peer(peer: dict) -> dict:
    """Project only discovery-safe fields for model-visible output."""

    return {
        "agent_id": peer.get("agent_id"),
        "name": peer.get("name"),
        "description": peer.get("description"),
        "capabilities": peer.get("capabilities", []),
        "trust": peer.get("trust"),
        "last_seen": peer.get("last_seen"),
    }


def _public_peers(peers: list[dict]) -> list[dict]:
    return [_public_peer(peer) for peer in peers]


def _run_or_schedule(coro):
    """Run an async service call from a sync tool without blocking an active loop."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    task = loop.create_task(coro)
    task.add_done_callback(_log_scheduled_failure)
    return {"status": "accepted", "detail": "Peer operation scheduled on the active Gateway loop."}


def _log_scheduled_failure(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Scheduled Alpha peer-network tool operation failed", exc_info=True)


@tool("alpha_peer_network", parse_docstring=True)
def alpha_peer_network_tool(
    runtime: Runtime,
    action: str,
    peer_id: str = "",
    message: str = "",
    conversation_id: str = "",
    mode: str = "direct",
    participants: list[str] | None = None,
) -> str:
    """Use the free Alpha-to-Alpha network.

    Actions:
      * ``discover`` — broadcast/refresh LAN discovery and list capability cards.
      * ``list`` — list already discovered or paired peers.
      * ``send`` — send a bounded message to ``peer_id``; the peer must be paired.
      * ``create`` — create a direct/group conversation from comma-separated participant ids.

    The response contains ids, capabilities, trust, and delivery status only. It
    never contains pairing codes, bearer tokens, or private filesystem paths.

    Args:
        runtime: Injected LangChain runtime context (unused by the installation-scoped service).
        action: One of ``discover``, ``list``, ``send``, or ``create``.
        peer_id: Target peer id for ``send`` or comma-separated ids for ``create``.
        message: Bounded text for ``send``.
        conversation_id: Optional existing conversation id.
        mode: Conversation topology for ``create`` (direct, one_to_many, many_to_one, many_to_many, broadcast).
        participants: Explicit participant ids for ``create``.
    """

    del runtime  # runtime is required for LangChain injection; network scope is installation-owned.
    normalized_action = action.strip().lower()
    service = get_peer_network_service()

    if normalized_action == "discover":
        result = _run_or_schedule(service.discover())
        if isinstance(result, dict):
            return json.dumps(result)
        safe_peers = _public_peers(result)
        return json.dumps({"peers": safe_peers, "count": len(safe_peers)}, ensure_ascii=False)
    if normalized_action == "list":
        result = _run_or_schedule(service.list_peers())
        if isinstance(result, dict):
            return json.dumps(result)
        safe_peers = _public_peers(result)
        return json.dumps({"peers": safe_peers, "count": len(safe_peers)}, ensure_ascii=False)
    if normalized_action == "send":
        if not peer_id.strip() or not message.strip():
            return "Error: send requires peer_id and message."
        request = MessageCreateRequest(
            conversation_id=conversation_id.strip() or None,
            recipients=[peer_id.strip()],
            text=message.strip(),
            kind="chat",
        )
        result = _run_or_schedule(service.send_message(request))
        return json.dumps(result if isinstance(result, dict) else {"status": "accepted"}, ensure_ascii=False)
    if normalized_action == "create":
        values = participants or [item.strip() for item in peer_id.split(",") if item.strip()]
        if not values:
            return "Error: create requires participants or a comma-separated peer_id value."
        result = _run_or_schedule(
            service.create_conversation(
                ConversationCreateRequest(
                    title="Agent-created Alpha peer conversation",
                    mode=mode,  # type: ignore[arg-type]
                    participants=values,
                )
            )
        )
        return json.dumps(result if isinstance(result, dict) else {"status": "accepted"}, ensure_ascii=False)
    return "Error: action must be discover, list, send, or create."


__all__ = ["alpha_peer_network_tool"]
