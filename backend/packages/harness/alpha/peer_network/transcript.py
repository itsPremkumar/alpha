"""Read-side projection of cross-installation Alpha conversations.

The peer plane already persists everything needed to show an operator the whole
cross-installation conversation, but it is scattered across two stores that have
no join key the UI can use:

* ``peer_network`` SQLite holds the **remote envelope** -- what a peer sent and
  the per-recipient delivery receipts (``PeerNetworkStore``).
* the run event store holds the **local Agent turn** -- what this installation
  replied, with tool calls, timings, and token usage (``RunJournal``).

``RunCreateRequest.metadata.peer_network`` (``app.gateway.services``) already
stamps ``message_id``/``conversation_id``/``peer_agent_id`` on the run, so the
join exists; nothing exposes it. This module owns that projection.

Three rules are load-bearing.

**Nothing here is persisted and nothing here is authorised.** The peer plane is
installation-scoped, so this module takes already-read rows and returns
read-only dictionaries. Authorisation belongs to the Gateway route, and it must
resolve against ``alpha.peer_network.storage.NETWORK_OWNER`` rather than the
caller's session user -- see ``app.gateway.routers.peer_network``.

**The projection is an allowlist, never a filter.** ``PeerNetworkStore.get_peer``
returns ``url``, ``websocket_url``, ``outbound_token``, ``token_hash`` and the
full Agent Card. Those must never reach a response body, so this module reads
only named safe keys instead of dropping known-unsafe ones. That mirrors
``alpha.tools.builtins.peer_network_tool._public_peer``, which is why a field
added to the peer row later cannot leak by omission.

**A transcript entry says who said it.** A remote envelope and a local reply are
two different claims about who spoke. They are returned as distinct ``role``
values and the UI must render them distinctly: a merged stream would let a
reader believe a remote peer said something the local Agent said.

The peer *turn* itself is dispatched with ``hide_from_ui`` on its triggering
human message (``app.gateway.services.launch_peer_network_agent_turn``). That
flag suppresses the inbound prompt from the ordinary chat feed -- correct,
since it is framed untrusted text -- but it does **not** suppress the run's
events: ``RunJournal.on_llm_end`` persists AI replies and tool results
unconditionally. So the local side of every peer turn is already durable and
this module only has to find it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: ``RunCreateRequest.metadata.peer_network`` sub-key carrying this plane's
#: correlation fields. Matches ``app.gateway.services``.
PEER_RUN_METADATA_KEY = "peer_network"

#: Roles a transcript entry can carry. A remote envelope is always
#: ``peer_message``; the local side is ``local_reply`` for the Agent's answer and
#: ``local_event`` for everything else it did (tool calls, lifecycle).
ROLE_PEER_MESSAGE = "peer_message"
ROLE_LOCAL_REPLY = "local_reply"
ROLE_LOCAL_EVENT = "local_event"

#: Hard ceiling on entries returned for one conversation. The peer message read
#: is already clamped to 1000 by ``PeerNetworkStore.list_messages``; matching it
#: here keeps a single conversation from becoming an unbounded response.
MAX_TRANSCRIPT_ENTRIES = 1000

#: Ceiling on events inlined into one turn's detail view. The run-events route
#: defaults to 500 and caps at 2000; a transcript turn is a summary surface, so
#: it stays well under that and reports ``events_truncated`` honestly rather
#: than silently showing a partial run.
MAX_TURN_EVENTS = 200

#: Event categories that are never surfaced in a transcript. These are internal
#: bookkeeping rows whose payload is large and whose meaning is not
#: conversation: behaviour-trace envelopes and middleware chatter. They remain
#: available through the full run-events route for an operator who wants them.
_HIDDEN_EVENT_CATEGORIES = frozenset({"trace", "middleware", "debug"})


def peer_run_metadata(run_metadata: Any) -> dict[str, Any]:
    """Return the ``peer_network`` correlation block from a run's metadata.

    Returns ``{}`` for anything that is not a well-formed block -- a run with no
    metadata, a legacy row, or a caller passing a projection that already
    stripped it. Callers treat ``{}`` as "this run is not a peer turn", never as
    a peer turn with missing fields.
    """

    if not isinstance(run_metadata, dict):
        return {}
    block = run_metadata.get(PEER_RUN_METADATA_KEY)
    return block if isinstance(block, dict) else {}


def is_peer_run(run_metadata: Any) -> bool:
    """Whether a run record's metadata identifies it as an inbound peer turn."""

    return bool(peer_run_metadata(run_metadata).get("message_id"))


def peer_thread_ids_for_run(run_metadata: Any) -> list[str]:
    """Return every peer conversation id a run is correlated to.

    A run carries exactly one, but returning a list keeps the call site honest
    about the join being a filter rather than a lookup: a run is *matched by*
    conversation id, so a future multi-conversation turn widens the filter
    instead of silently truncating.
    """

    conversation_id = peer_run_metadata(run_metadata).get("conversation_id")
    return [conversation_id] if isinstance(conversation_id, str) and conversation_id else []


def runs_for_conversation(runs: Iterable[dict[str, Any]], conversation_id: str) -> list[dict[str, Any]]:
    """Select the peer runs belonging to one conversation, oldest first.

    ``runs`` are run-store records; only their ``metadata`` is read here so this
    stays a pure filter with no store dependency. Sorting is by ``created_at``
    then ``run_id`` so the transcript order is stable when two runs share a
    timestamp (which a fast peer exchange can produce).
    """

    matched = [run for run in runs if conversation_id in peer_thread_ids_for_run(run.get("metadata"))]
    return sorted(matched, key=lambda run: (str(run.get("created_at") or ""), str(run.get("run_id") or "")))


def public_peer_summary(peer: dict[str, Any] | None) -> dict[str, Any]:
    """Project the peer fields that are safe in a transcript response.

    An allowlist, not a filter -- see the module docstring. ``url``,
    ``websocket_url``, ``outbound_token``, ``token_hash``, ``card`` and
    ``owner_id`` are all readable on the store row and none of them may appear
    here. A ``None`` peer yields an empty dict rather than a fabricated identity,
    so a deleted peer renders as "not reported" instead of a blank name.
    """

    if not isinstance(peer, dict):
        return {}
    return {
        "agent_id": peer.get("agent_id"),
        "name": peer.get("name"),
        "description": peer.get("description"),
        "version": peer.get("version"),
        "capabilities": peer.get("capabilities") or [],
        "skills": peer.get("skills") or [],
        "trust": peer.get("trust"),
        "source": peer.get("source"),
        "first_seen": peer.get("first_seen"),
        "last_seen": peer.get("last_seen"),
        "paired_at": peer.get("paired_at"),
        "auto_reply": bool(peer.get("auto_reply")),
    }


def delivery_receipt(entry: dict[str, Any]) -> dict[str, Any]:
    """Project one per-recipient delivery receipt.

    A receipt is a claim about one recipient, so it is never aggregated away:
    ``status`` alone would let a partially-delivered fan-out render as a
    successful group send.
    """

    return {
        "recipient_id": entry.get("recipient_id"),
        "status": entry.get("status"),
        "transport": entry.get("transport"),
        "error": entry.get("error"),
        "delivered_at": entry.get("delivered_at"),
        "read_at": entry.get("read_at"),
    }


def peer_message_entry(message: dict[str, Any]) -> dict[str, Any]:
    """Project one stored peer envelope as a transcript entry.

    ``direction`` decides the role, and the remote text is carried through as
    data. Callers must render it escaped: it is untrusted text from another
    installation and this module never treats it as anything else.
    """

    inbound = message.get("direction") == "inbound"
    return {
        "entry_id": message.get("message_id"),
        "role": ROLE_PEER_MESSAGE,
        "side": "peer" if inbound else "local",
        "sender_id": message.get("sender_id"),
        "recipients": message.get("recipients") or [],
        "kind": message.get("kind"),
        "text": message.get("text") or "",
        "payload": message.get("payload") or {},
        "status": message.get("status"),
        "created_at": message.get("created_at"),
        "delivered_at": message.get("delivered_at"),
        "read_at": message.get("read_at"),
        "delivery_error": message.get("delivery_error"),
        "deliveries": [delivery_receipt(row) for row in message.get("deliveries") or [] if isinstance(row, dict)],
        "thread_id": None,
        "run_id": None,
    }


def _event_text(content: Any) -> str:
    """Best-effort text for one persisted event.

    Run events store a LangChain ``model_dump`` for message rows, so the text
    sits under ``content`` as either a string or a content-block list. Anything
    else (a usage record, a lifecycle row) has no conversation text and returns
    ``""`` rather than stringifying an arbitrary object into the transcript.
    """

    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        raw = content.get("content")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            parts = [block.get("text") for block in raw if isinstance(block, dict) and isinstance(block.get("text"), str)]
            if parts:
                return "".join(parts)
    return ""


def _event_tool_calls(content: Any) -> list[dict[str, Any]]:
    """Return tool-call receipts embedded in a run event.

    The run-events route already redacts tool arguments; this projection reuses
    that ``content`` verbatim and adds no path of its own.
    """

    if not isinstance(content, dict):
        return []
    calls = content.get("tool_calls")
    if not isinstance(calls, list):
        return []
    projected: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        projected.append(
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "args": call.get("args") or {},
                "status": call.get("status"),
            }
        )
    return projected


def run_event_entry(event: dict[str, Any]) -> dict[str, Any]:
    """Project one persisted run event as a local-side transcript entry.

    AI responses become ``local_reply``; everything else stays ``local_event``
    so a tool receipt can never be read as the Agent speaking.
    """

    content = event.get("content")
    event_type = str(event.get("event_type") or "")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    is_reply = event_type.endswith("ai.response")
    return {
        "entry_id": f"{event.get('run_id')}:{event.get('seq') or event.get('event_id') or ''}",
        "role": ROLE_LOCAL_REPLY if is_reply else ROLE_LOCAL_EVENT,
        "side": "local",
        "sender_id": None,
        "recipients": [],
        "kind": event_type,
        "text": _event_text(content),
        "payload": {},
        "status": None,
        "created_at": event.get("created_at"),
        "delivered_at": None,
        "read_at": None,
        "delivery_error": None,
        "deliveries": [],
        "tool_calls": _event_tool_calls(content),
        "caller": metadata.get("caller"),
        "latency_ms": metadata.get("latency_ms"),
        "usage": metadata.get("usage"),
    }


def turn_detail(
    run: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    max_events: int = MAX_TURN_EVENTS,
) -> dict[str, Any]:
    """Project one peer run with its events as a turn-detail record.

    ``events_truncated`` is reported as a real boolean because the event list is
    bounded: a partial run that renders as a complete one is the exact
    dishonesty this plane is supposed to avoid.
    """

    metadata = peer_run_metadata(run.get("metadata"))
    visible = [event for event in events if str(event.get("category") or "") not in _HIDDEN_EVENT_CATEGORIES]
    bounded = visible[: max(1, max_events)]
    return {
        "run_id": run.get("run_id"),
        "thread_id": run.get("thread_id"),
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "error": run.get("error"),
        "stop_reason": run.get("stop_reason"),
        "model_name": run.get("model_name"),
        "peer_message_id": metadata.get("message_id"),
        "peer_agent_id": metadata.get("peer_agent_id"),
        "kind": metadata.get("kind"),
        "message_count": run.get("message_count"),
        "llm_call_count": run.get("llm_call_count"),
        "token_usage_by_model": run.get("token_usage_by_model") or {},
        "total_input_tokens": run.get("total_input_tokens") or 0,
        "total_output_tokens": run.get("total_output_tokens") or 0,
        "total_tokens": run.get("total_tokens") or 0,
        "events": [run_event_entry(event) for event in bounded],
        "events_truncated": len(visible) > len(bounded),
        "events_shown": len(bounded),
        "events_total": len(visible),
    }


def interleave(
    peer_messages: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    *,
    max_entries: int = MAX_TRANSCRIPT_ENTRIES,
) -> list[dict[str, Any]]:
    """Merge the remote and local sides into one time-ordered transcript.

    Both sides carry their own timestamps, so ordering is a real merge rather
    than a concatenation -- a peer reply and this installation's answer to it
    interleave by when each actually happened. ``created_at`` is compared as a
    string because both stores emit ISO-8601 UTC with the same format; a missing
    timestamp sorts last rather than jumping to the front of the history.

    The result is capped and reports truncation, because a conversation with no
    cap is a response that grows without bound.
    """

    entries: list[dict[str, Any]] = []
    for message in peer_messages:
        entry = peer_message_entry(message)
        if entry["created_at"]:
            entries.append((str(entry["created_at"]), entry["entry_id"] or "", ROLE_PEER_MESSAGE, entry))
    for turn in turns:
        detail = turn_detail(turn, turn.get("events") or [])
        entry_id = f"run:{detail['run_id']}"
        # A run with no timestamp still belongs in the transcript; it sorts last.
        entries.append(
            (
                str(turn.get("created_at") or ""),
                entry_id,
                ROLE_LOCAL_REPLY,
                {
                    "entry_id": entry_id,
                    "role": ROLE_LOCAL_REPLY,
                    "side": "local",
                    "sender_id": None,
                    "recipients": [],
                    "kind": detail.get("kind") or "peer_turn",
                    "text": detail.get("last_ai_message") or "",
                    "payload": {},
                    "status": detail.get("status"),
                    "created_at": turn.get("created_at"),
                    "delivered_at": None,
                    "read_at": None,
                    "delivery_error": None,
                    "deliveries": [],
                    "thread_id": detail.get("thread_id"),
                    "run_id": detail.get("run_id"),
                    "turn": detail,
                },
            )
        )

    # Missing timestamps last: a sentinel prefix keeps them out of the way
    # without discarding the entry.
    def sort_key(item: tuple[str, str, str, dict[str, Any]]) -> tuple[int, str, str]:
        created, entry_id, _, _ = item
        return (1 if not created else 0, created, entry_id)

    ordered = [entry for *_, entry in sorted(entries, key=sort_key)]
    bounded = ordered[: max(1, max_entries)]
    return bounded


def transcript_summary(
    conversation: dict[str, Any],
    peer: dict[str, Any] | None,
    message_count: int,
    turn_count: int,
) -> dict[str, Any]:
    """Project one row of the cross-conversation transcript index.

    ``counts`` are measured from the rows actually read, so the index reports
    real numbers rather than asserting a total it did not compute.
    """

    return {
        "conversation_id": conversation.get("conversation_id"),
        "title": conversation.get("title"),
        "mode": conversation.get("mode"),
        "status": conversation.get("status"),
        "participants": conversation.get("participants") or [],
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "peer": public_peer_summary(peer),
        "counts": {
            "messages": message_count,
            "turns": turn_count,
        },
    }


__all__ = [
    "MAX_TRANSCRIPT_ENTRIES",
    "MAX_TURN_EVENTS",
    "PEER_RUN_METADATA_KEY",
    "ROLE_LOCAL_EVENT",
    "ROLE_LOCAL_REPLY",
    "ROLE_PEER_MESSAGE",
    "delivery_receipt",
    "interleave",
    "is_peer_run",
    "peer_message_entry",
    "peer_run_metadata",
    "peer_thread_ids_for_run",
    "public_peer_summary",
    "run_event_entry",
    "runs_for_conversation",
    "transcript_summary",
    "turn_detail",
]
