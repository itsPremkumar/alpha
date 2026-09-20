"""Unit tests for Inbound Message Debouncer and Turn Batcher."""


import pytest

from alpha.channels.debounce.debouncer import InboundDebouncer


def test_inbound_debouncer_flush():
    debouncer = InboundDebouncer(debounce_seconds=0.1)

    # Push 3 rapid messages
    q1 = debouncer.push("session_a", "alice", "Hello")
    q2 = debouncer.push("session_a", "alice", "are you there?")
    q3 = debouncer.push("session_a", "bob", "ping")

    assert q3 == 3

    # Flush
    turn = debouncer.flush("session_a")
    assert turn is not None
    assert turn.message_count == 3
    assert turn.senders == ["alice", "bob"]
    assert turn.merged_content == "Hello\nare you there?\nping"

    # Subsequent flush is None
    assert debouncer.flush("session_a") is None


@pytest.mark.asyncio
async def test_inbound_debouncer_async_wait():
    debouncer = InboundDebouncer(debounce_seconds=0.05)

    debouncer.push("session_b", "user", "Message 1")
    debouncer.push("session_b", "user", "Message 2")

    turn = await debouncer.wait_and_flush("session_b")
    assert turn is not None
    assert turn.message_count == 2
    assert "Message 1\nMessage 2" == turn.merged_content
