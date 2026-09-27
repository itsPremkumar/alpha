"""rt-auditor: `limit=0` parity across the non-SQL run-event stores.

``RunEventStore.list_messages`` / ``list_messages_by_run`` are one interface with
three implementations. SQL backends emit ``LIMIT 0``, which returns zero rows, so
``limit=0`` means "no items". The memory and JSONL backends slice with
``rows[-limit:]``; Python evaluates ``-0 == 0``, so ``rows[-0:]`` is ``rows[0:]``
and a caller asking for zero messages gets the *entire* thread back.
"""

from __future__ import annotations

import pytest

from alpha.runtime.events.store.memory import MemoryRunEventStore

THREAD = "t1"
SEQS = (1, 2, 3, 4, 5)


def _seed(store, *, thread_id: str = THREAD, run_id: str = "r1") -> None:
    for seq in SEQS:
        store._put_one(
            thread_id=thread_id,
            run_id=run_id,
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "id": f"msg-{seq}", "content": f"reply {seq}"},
        )


@pytest.mark.asyncio
async def test_memory_list_messages_with_limit_zero_returns_nothing():
    store = MemoryRunEventStore()
    _seed(store)
    rows = await store.list_messages(THREAD, limit=0, user_id=None)
    assert rows == [], f"limit=0 must return no messages, got {len(rows)}: {[r['seq'] for r in rows]}"


@pytest.mark.asyncio
async def test_memory_list_messages_by_run_with_limit_zero_returns_nothing():
    store = MemoryRunEventStore()
    _seed(store)
    rows = await store.list_messages_by_run(THREAD, "r1", limit=0)
    assert rows == [], f"limit=0 must return no messages, got {len(rows)}: {[r['seq'] for r in rows]}"


@pytest.mark.asyncio
async def test_memory_limit_zero_with_a_before_cursor_returns_nothing():
    store = MemoryRunEventStore()
    _seed(store)
    rows = await store.list_messages(THREAD, before_seq=5, limit=0, user_id=None)
    assert rows == []


@pytest.mark.asyncio
async def test_memory_limit_zero_with_an_after_cursor_returns_nothing():
    store = MemoryRunEventStore()
    _seed(store)
    rows = await store.list_messages(THREAD, after_seq=1, limit=0, user_id=None)
    assert rows == []


@pytest.mark.asyncio
async def test_memory_still_honours_a_positive_limit():
    """Control: the zero case must not be fixed by breaking the normal one."""
    store = MemoryRunEventStore()
    _seed(store)
    assert [r["seq"] for r in await store.list_messages(THREAD, limit=2, user_id=None)] == [4, 5]
    assert [r["seq"] for r in await store.list_messages(THREAD, user_id=None)] == list(SEQS)
    assert [r["seq"] for r in await store.list_messages(THREAD, before_seq=5, limit=2, user_id=None)] == [3, 4]
    assert [r["seq"] for r in await store.list_messages(THREAD, after_seq=3, limit=2, user_id=None)] == [4, 5]
    assert [r["seq"] for r in await store.list_messages_by_run(THREAD, "r1", limit=2)] == [4, 5]
    assert [r["seq"] for r in await store.list_messages_by_run(THREAD, "r1")] == list(SEQS)


@pytest.mark.asyncio
async def test_jsonl_limit_zero_returns_nothing(tmp_path):
    from alpha.runtime.events.store.jsonl import JsonlRunEventStore

    store = JsonlRunEventStore(base_dir=tmp_path)
    for seq in SEQS:
        await store.put(
            thread_id=THREAD,
            run_id="r1",
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "id": f"msg-{seq}", "content": f"reply {seq}"},
        )

    assert await store.list_messages(THREAD, limit=0, user_id=None) == [], "JSONL list_messages(limit=0) must return no messages"
    assert await store.list_messages(THREAD, before_seq=5, limit=0, user_id=None) == [], "JSONL list_messages(before_seq, limit=0) must return no messages"
    assert await store.list_messages_by_run(THREAD, "r1", limit=0) == [], "JSONL list_messages_by_run(limit=0) must return no messages"
    # Control: positive limits still page correctly.
    assert [r["seq"] for r in await store.list_messages(THREAD, limit=2, user_id=None)] == [4, 5]
    assert [r["seq"] for r in await store.list_messages(THREAD, before_seq=5, limit=2, user_id=None)] == [3, 4]
    assert [r["seq"] for r in await store.list_messages(THREAD, after_seq=3, limit=2, user_id=None)] == [4, 5]
    assert [r["seq"] for r in await store.list_messages_by_run(THREAD, "r1", limit=2)] == [4, 5]
