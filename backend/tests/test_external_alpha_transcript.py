"""Contract tests for the peer transcript projection.

The peer plane already persists both sides of a cross-installation conversation:
the remote envelope in ``peer_network`` SQLite and the local Agent turn in the
run event store. These tests pin the read-side projection that joins them, and
in particular the three things that would make an External Alpha tab lie:

1. a remote envelope and a local reply stay **distinctly attributed**;
2. the projection is an **allowlist**, so a credential or endpoint cannot leak
   by a field being added to the peer row later;
3. a bounded response **reports** its truncation instead of looking complete.

Pure-function tests, no FastAPI and no store: ``transcript`` deliberately knows
nothing about either.
"""

from __future__ import annotations

from alpha.peer_network import transcript as T


def _message(**overrides):
    base = {
        "message_id": "msg_1",
        "conversation_id": "conv_1",
        "sender_id": "alpha_remote",
        "recipients": ["alpha_local"],
        "kind": "chat",
        "text": "hello from the other installation",
        "payload": {},
        "status": "delivered",
        "direction": "inbound",
        "created_at": "2026-01-01T00:00:00+00:00",
        "delivered_at": "2026-01-01T00:00:00+00:00",
        "read_at": None,
        "delivery_error": None,
        "deliveries": [{"recipient_id": "alpha_local", "status": "delivered", "transport": "http", "error": None, "delivered_at": "2026-01-01T00:00:00+00:00", "read_at": None}],
    }
    base.update(overrides)
    return base


def _run(run_id="run_1", created_at="2026-01-01T00:00:05+00:00", **overrides):
    base = {
        "run_id": run_id,
        "thread_id": "peer_abc123",
        "status": "success",
        "created_at": created_at,
        "updated_at": created_at,
        "error": None,
        "stop_reason": None,
        "model_name": "union-alpha",
        "message_count": 2,
        "llm_call_count": 1,
        "token_usage_by_model": {"union-alpha": {"input_tokens": 10, "output_tokens": 20}},
        "total_input_tokens": 10,
        "total_output_tokens": 20,
        "total_tokens": 30,
        "last_ai_message": "reply from the local Alpha",
        "metadata": {
            "peer_network": {
                "message_id": "msg_1",
                "conversation_id": "conv_1",
                "peer_agent_id": "alpha_remote",
                "kind": "chat",
            }
        },
        "events": [],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------


def test_peer_run_metadata_reads_the_correlation_block():
    assert T.peer_run_metadata({"peer_network": {"message_id": "m"}}) == {"message_id": "m"}


def test_peer_run_metadata_is_empty_for_non_peer_runs():
    """A personal chat run must not be mistaken for a peer turn.

    ``{}`` is the honest answer for a run with no peer metadata, and callers
    treat it as "not a peer turn" rather than as a peer turn missing fields.
    """

    assert T.peer_run_metadata({}) == {}
    assert T.peer_run_metadata({"other": {"message_id": "m"}}) == {}
    assert T.peer_run_metadata(None) == {}
    assert T.peer_run_metadata("not-a-dict") == {}


def test_is_peer_run():
    assert T.is_peer_run({"peer_network": {"message_id": "m"}}) is True
    assert T.is_peer_run({"peer_network": {}}) is False
    assert T.is_peer_run({}) is False


def test_runs_for_conversation_selects_only_matching_peers():
    runs = [
        _run("run_1"),
        _run("run_2", metadata={"peer_network": {"message_id": "m2", "conversation_id": "conv_other"}}),
        _run("run_3", metadata={}),
    ]
    assert [r["run_id"] for r in T.runs_for_conversation(runs, "conv_1")] == ["run_1"]


def test_runs_for_conversation_orders_oldest_first_with_a_stable_tiebreak():
    """Two runs sharing a timestamp must still order deterministically.

    A fast peer exchange can produce identical `created_at` values; ordering by
    run id keeps the transcript stable instead of shuffling between reloads.
    """

    runs = [_run("run_b", created_at="2026-01-01T00:00:05+00:00"), _run("run_a", created_at="2026-01-01T00:00:05+00:00")]
    assert [r["run_id"] for r in T.runs_for_conversation(runs, "conv_1")] == ["run_a", "run_b"]


# --------------------------------------------------------------------------
# Attribution: who said it
# --------------------------------------------------------------------------


def test_peer_message_entry_is_attributed_to_the_peer_side():
    entry = T.peer_message_entry(_message())
    assert entry["role"] == T.ROLE_PEER_MESSAGE
    assert entry["side"] == "peer"


def test_outbound_envelope_is_still_a_peer_message_role():
    """Direction flips `side`, not the speaker.

    An outbound envelope was authored *at* a peer installation and delivered
    here, so it is peer-authored text on this installation's wire.
    """

    entry = T.peer_message_entry(_message(direction="outbound"))
    assert entry["side"] == "local"
    assert entry["role"] == T.ROLE_PEER_MESSAGE


def test_interleave_keeps_remote_and_local_distinct():
    """The load-bearing honesty rule.

    A merged stream would let a reader believe a remote peer said something the
    local Agent said, so the two sides must never collapse into one role.
    """

    entries = T.interleave([_message()], [_run()])
    assert [e["role"] for e in entries] == [T.ROLE_PEER_MESSAGE, T.ROLE_LOCAL_REPLY]
    assert entries[0]["sender_id"] == "alpha_remote"
    assert entries[1]["sender_id"] is None
    assert entries[1]["run_id"] == "run_1"


def test_interleave_orders_by_actual_time_not_by_storage():
    """The local turn happened after the envelope, so it must read second."""

    entries = T.interleave([_message(created_at="2026-01-01T00:00:00+00:00")], [_run(created_at="2026-01-01T00:00:05+00:00")])
    assert entries[0]["role"] == T.ROLE_PEER_MESSAGE
    assert entries[1]["role"] == T.ROLE_LOCAL_REPLY


def test_interleave_puts_undated_runs_last_rather_than_first():
    """A missing timestamp must not jump to the front of the history."""

    entries = T.interleave([_message(created_at="2026-01-01T00:00:00+00:00")], [_run(created_at=None)])
    assert entries[-1]["role"] == T.ROLE_LOCAL_REPLY


def test_interleave_carries_delivery_receipts_per_recipient():
    entries = T.interleave([_message()], [])
    assert entries[0]["deliveries"] == [{"recipient_id": "alpha_local", "status": "delivered", "transport": "http", "error": None, "delivered_at": "2026-01-01T00:00:00+00:00", "read_at": None}]


def test_a_failed_recipient_is_not_aggregated_into_success():
    """One failed recipient must stay visible; a fan-out is per-recipient."""

    message = _message(
        deliveries=[
            {"recipient_id": "a", "status": "delivered"},
            {"recipient_id": "b", "status": "failed", "error": "offline"},
        ]
    )
    receipts = T.interleave([message], [])[0]["deliveries"]
    assert [r["status"] for r in receipts] == ["delivered", "failed"]
    assert receipts[1]["error"] == "offline"


# --------------------------------------------------------------------------
# Allowlist: a credential must never reach a response body
# --------------------------------------------------------------------------


def test_public_peer_summary_never_exposes_a_credential_or_endpoint():
    """Allowlist, not filter.

    `PeerNetworkStore.get_peer` returns every one of these fields, so asserting
    they are *absent* is what proves the projection reads only named safe keys.
    A field added to the peer row later cannot leak by omission.
    """

    row = {
        "agent_id": "alpha_remote",
        "name": "Remote",
        "description": "d",
        "version": "1.0.0",
        "capabilities": ["chat"],
        "skills": [],
        "trust": "paired",
        "source": "pairing",
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-01T00:00:00+00:00",
        "paired_at": "2026-01-01T00:00:00+00:00",
        "auto_reply": True,
        # Everything below must be dropped.
        "url": "http://192.168.1.20:8001",
        "websocket_url": "ws://192.168.1.20:8001/api/peer-network/ws",
        "outbound_token": "super-secret-bearer",
        "token_hash": "deadbeef",
        "card": {"pairing_code": "leaked"},
        "owner_id": "installation",
    }
    summary = T.public_peer_summary(row)
    for leaked in ("url", "websocket_url", "outbound_token", "token_hash", "card", "owner_id"):
        assert leaked not in summary, leaked
    assert summary["agent_id"] == "alpha_remote"
    assert summary["auto_reply"] is True


def test_public_peer_summary_of_a_deleted_peer_is_empty_not_fabricated():
    """A removed peer renders as "not reported", never as a blank identity."""

    assert T.public_peer_summary(None) == {}
    assert T.public_peer_summary("not-a-dict") == {}


# --------------------------------------------------------------------------
# Run events -> local-side entries
# --------------------------------------------------------------------------


def test_ai_response_becomes_a_local_reply():
    event = {
        "run_id": "run_1",
        "seq": 4,
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {"type": "ai", "content": "here is my reply"},
        "metadata": {"caller": "lead_agent", "usage": {"total_tokens": 30}, "latency_ms": 120},
        "created_at": "2026-01-01T00:00:06+00:00",
    }
    entry = T.run_event_entry(event)
    assert entry["role"] == T.ROLE_LOCAL_REPLY
    assert entry["text"] == "here is my reply"
    assert entry["caller"] == "lead_agent"
    assert entry["latency_ms"] == 120
    assert entry["usage"] == {"total_tokens": 30}


def test_a_tool_result_is_never_rendered_as_the_agent_speaking():
    event = {
        "run_id": "run_1",
        "seq": 5,
        "event_type": "llm.tool.result",
        "category": "message",
        "content": {"type": "tool", "content": "wrote a file"},
        "metadata": {},
        "created_at": "2026-01-01T00:00:07+00:00",
    }
    assert T.run_event_entry(event)["role"] == T.ROLE_LOCAL_EVENT


def test_ai_content_blocks_are_flattened_to_text():
    event = {
        "run_id": "r",
        "seq": 1,
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {"type": "ai", "content": [{"type": "text", "text": "part one "}, {"type": "text", "text": "part two"}]},
        "metadata": {},
    }
    assert T.run_event_entry(event)["text"] == "part one part two"


def test_a_non_message_event_yields_no_conversation_text():
    """An arbitrary object must never be stringified into the transcript."""

    event = {"run_id": "r", "seq": 1, "event_type": "run.started", "category": "lifecycle", "content": {"nested": {"deep": True}}, "metadata": {}}
    assert T.run_event_entry(event)["text"] == ""


def test_tool_calls_are_projected_with_status():
    event = {
        "run_id": "r",
        "seq": 2,
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {"type": "ai", "content": "calling", "tool_calls": [{"id": "c1", "name": "shell", "args": {"cmd": "ls"}, "status": "completed"}]},
        "metadata": {},
    }
    calls = T.run_event_entry(event)["tool_calls"]
    assert calls == [{"id": "c1", "name": "shell", "args": {"cmd": "ls"}, "status": "completed"}]


# --------------------------------------------------------------------------
# Turn detail
# --------------------------------------------------------------------------


def test_turn_detail_reports_correlation_and_usage():
    detail = T.turn_detail(_run(), [])
    assert detail["peer_message_id"] == "msg_1"
    assert detail["peer_agent_id"] == "alpha_remote"
    assert detail["run_id"] == "run_1"
    assert detail["total_tokens"] == 30
    assert detail["events_truncated"] is False


def test_turn_detail_reports_truncation_honestly():
    """A partial run must never render as a complete one."""

    events = [{"run_id": "r", "seq": i, "event_type": "llm.ai.response", "category": "message", "content": "x", "metadata": {}} for i in range(10)]
    detail = T.turn_detail(_run(), events, max_events=3)
    assert detail["events_truncated"] is True
    assert detail["events_shown"] == 3
    assert detail["events_total"] == 10


def test_turn_detail_hides_internal_trace_and_middleware_rows():
    """Trace/debug chatter is not conversation.

    It stays available through the full run-events route; a transcript turn is a
    summary surface and must not carry those rows.
    """

    events = [
        {"run_id": "r", "seq": 1, "event_type": "llm.ai.response", "category": "message", "content": "real reply", "metadata": {}},
        {"run_id": "r", "seq": 2, "event_type": "trace.span", "category": "trace", "content": {"big": "payload"}, "metadata": {}},
        {"run_id": "r", "seq": 3, "event_type": "mw", "category": "middleware", "content": "chatter", "metadata": {}},
        {"run_id": "r", "seq": 4, "event_type": "dbg", "category": "debug", "content": "noise", "metadata": {}},
    ]
    detail = T.turn_detail(_run(), events)
    assert [e["text"] for e in detail["events"]] == ["real reply"]
    assert detail["events_total"] == 1


def test_a_run_whose_events_are_gone_still_appears():
    """Retention or a pruned store must not erase the turn's existence."""

    detail = T.turn_detail(_run(), [])
    assert detail["events"] == []
    assert detail["events_truncated"] is False
    assert detail["peer_message_id"] == "msg_1"


# --------------------------------------------------------------------------
# Index summary
# --------------------------------------------------------------------------


def test_transcript_summary_counts_are_measured_not_asserted():
    conversation = {
        "conversation_id": "conv_1",
        "title": "Alpha peer room",
        "mode": "direct",
        "status": "active",
        "participants": ["alpha_remote", "alpha_local"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:05+00:00",
    }
    summary = T.transcript_summary(conversation, {"agent_id": "alpha_remote", "name": "Remote"}, message_count=7, turn_count=2)
    assert summary["counts"] == {"messages": 7, "turns": 2}
    assert summary["conversation_id"] == "conv_1"
    assert summary["peer"]["agent_id"] == "alpha_remote"
