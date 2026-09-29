"""Honesty 5/6 — every run-event ``created_at`` is a string on the way out.

The defect this pins
--------------------
``GET /projects/{id}/events`` emitted a raw epoch float where every sibling
route emitted ISO 8601, so the client rendered "time not reported" for every
row. The client was not wrong: it was reading a non-date. The fix coerced at the
API boundary.

How it was found
----------------
By rendering the Projects view against a live Gateway and noticing every activity
row was undated while the server was plainly sending something.

The class of bug this file generalises
--------------------------------------
The project route was fixed *at that one boundary*. The same shape can occur
anywhere a value crosses from a store into a response, and the run-event feed has
three backends with three different write paths and three different read paths.
``contracts/run_event_stream_contract.json`` declares
``"created_at": {"type": "string", "format": "date-time"}``, and
``tests/test_run_event_stream_contract.py`` checks that the contract, the
producer catalog and ``alpha/constants.py`` agree — but it checks the *field
exists and the constants match*, not the *type of the value a reader receives*.
A contract nobody checks the runtime against is a comment.

What is asserted here
---------------------
For every backend (memory, db, jsonl), a value written through the real
producer path comes back through the real read path as a timezone-aware ISO
string, and the string is what the Gateway's own run-events handler returns.

One assertion is currently **not** satisfiable and is pinned as a known defect
rather than quietly dropped: a legacy float already on disk is not healed on the
read path for the process-local backends. See
:func:`test_a_legacy_float_created_at_cannot_reach_a_reader_memory_store` and
:func:`test_a_legacy_float_created_at_cannot_reach_a_reader_jsonl_store`; the
report accompanying this file describes the two code sites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from alpha.persistence.engine import close_engine, get_session_factory, init_engine
from alpha.runtime.events.store.jsonl import JsonlRunEventStore
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal

THREAD_ID = "honesty-created-at"
RUN_ID = "honesty-created-at-run"

#: A legacy epoch float of exactly the shape older Alpha versions wrote, per
#: ``alpha.utils.time.coerce_iso``'s own docstring ("records that historically
#: stored ``str(time.time())`` floats").
LEGACY_FLOAT = 1_756_000_000.123


def _assert_aware_iso(value: object, *, where: str) -> None:
    """The contract's own words: a ``string`` with ``format: date-time``."""
    assert isinstance(value, str), f"{where}: created_at is {type(value).__name__} ({value!r}), not a string; every sibling surface emits ISO 8601"
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{where}: {value!r} parses but carries no timezone, so a client cannot place it"


def _journal_events(store: Any) -> list[dict]:
    """Events the real ``RunJournal`` wrote, read back through the real read path."""
    return anyio.run(lambda: store.list_events(THREAD_ID, RUN_ID))


def _drive_real_journal(store: Any) -> list[dict]:
    """Run the real journal's callbacks against *store*, then read back.

    ``on_chain_start`` / ``on_chat_model_start`` / ``on_llm_end`` /
    ``on_tool_end`` / ``on_chain_end`` are the callbacks that produce the run
    events a real run writes — the four producer groups the run-event contract
    enumerates — so the values asserted below are the product's, not fixtures.
    """
    from uuid import uuid4

    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    journal = RunJournal(run_id=RUN_ID, thread_id=THREAD_ID, event_store=store)
    human = {"role": "user", "content": "hello"}
    ai_message = AIMessage(content="hi", id="ai-1", response_metadata={"model_name": "test-model"}, usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
    ai = {"role": "assistant", "content": "hi"}
    response = LLMResult(generations=[[ChatGeneration(message=ai_message)]])

    journal.on_chain_start({"name": "agent"}, [human], run_id=uuid4())
    journal.on_chat_model_start({"name": "model"}, [[human]], run_id=uuid4())
    journal.on_llm_end(response, run_id=uuid4(), parent_run_id=uuid4())
    journal.on_tool_end(ToolMessage(content="tool output", tool_call_id="tc-1", name="echo", id="tm-1"), run_id=uuid4(), parent_run_id=uuid4())
    journal.on_chain_end({"messages": [human, ai]}, run_id=uuid4())
    anyio.run(journal.flush)
    return _journal_events(store)


# ---------------------------------------------------------------------------
# The contract, read as a consumer reads it
# ---------------------------------------------------------------------------


def test_the_contract_declares_created_at_as_a_date_time_string():
    """The premise: the contract says what the tests below then enforce.

    Read from the shipped JSON contract, not from a Python constant, so a change
    to the contract that loosens the type has to be acknowledged here. This is
    the file the existing contract-conformance test does *not* read for type:
    it validates the *schema* but the schema is only ever applied to records
    that the producer happened to build correctly.
    """
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    contract = json.loads((repo_root / "contracts" / "run_event_stream_contract.json").read_text(encoding="utf-8"))
    field = contract["record_schema"]["properties"]["created_at"]
    assert field.get("type") == "string", field
    assert field.get("format") == "date-time", field


def test_created_at_is_a_required_field():
    """A field nothing requires is a field nothing writes."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    contract = json.loads((repo_root / "contracts" / "run_event_stream_contract.json").read_text(encoding="utf-8"))
    assert "created_at" in contract["record_schema"]["required"], "created_at is no longer a required run-event field"


# ---------------------------------------------------------------------------
# Per-backend: the real producer, the real read path, the real type
# ---------------------------------------------------------------------------


def test_memory_backend_emits_aware_iso_strings():
    rows = _drive_real_journal(MemoryRunEventStore())
    assert rows, "the journal produced no run events"
    for row in rows:
        _assert_aware_iso(row["created_at"], where=f"memory/{row['event_type']}")


def test_jsonl_backend_emits_aware_iso_strings(tmp_path):
    rows = _drive_real_journal(JsonlRunEventStore(tmp_path / "events"))
    assert rows, "the journal produced no run events"
    for row in rows:
        _assert_aware_iso(row["created_at"], where=f"jsonl/{row['event_type']}")


@pytest.fixture()
def db_store(tmp_path):
    async def _init() -> None:
        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'events.db'}", sqlite_dir=str(tmp_path))

    anyio.run(_init)
    try:
        from alpha.runtime.events.store.db import DbRunEventStore

        yield DbRunEventStore(get_session_factory())
    finally:
        anyio.run(close_engine)


def test_db_backend_emits_aware_iso_strings(db_store):
    """The backend whose read path coerces ``datetime`` to ISO itself.

    Worth pinning separately because it is the *only* backend that heals a
    naive/``datetime`` value today, which is why the two legacy-float tests
    below fail while this one passes. The difference is the whole point.
    """
    rows = _drive_real_journal(db_store)
    assert rows, "the journal produced no run events"
    for row in rows:
        _assert_aware_iso(row["created_at"], where=f"db/{row['event_type']}")


def test_the_gateway_run_events_handler_returns_strings(db_store):
    """The last hop: what ``GET /threads/{id}/runs/{id}/events`` actually serves.

    ``list_run_events`` is called here exactly as the existing route tests call
    it — directly, as a coroutine, with a request whose ``app.state`` carries the
    real store — so this is the value a client receives, not an intermediate.
    """
    import asyncio
    from unittest.mock import MagicMock

    from app.gateway.routers import thread_runs

    _drive_real_journal(db_store)

    class _Request:
        app = MagicMock()
        _alpha_test_bypass_auth = True

    _Request.app.state.run_event_store = db_store

    # No user-context override: the db store stamps `user_id` from the
    # contextvar on write and filters on it on read, so the read must run as the
    # same user the write did. The conftest autouse fixture already bound one.
    events = asyncio.run(
        thread_runs.list_run_events(
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            request=_Request(),
            event_types=None,
            task_id=None,
            limit=500,
            after_seq=None,
        )
    )
    assert events, "the run-events handler returned nothing for a run the journal had written to"
    for event in events:
        _assert_aware_iso(event["created_at"], where=f"api/{event['event_type']}")


# ---------------------------------------------------------------------------
# A float cannot appear
# ---------------------------------------------------------------------------


def test_a_float_cannot_be_written_at_all():
    """The store's own contract types ``created_at`` as ``str | None``.

    Asserted as a *type* on the abstract interface rather than on one
    implementation: three backends share this signature, and the base class is
    the one place a fourth backend would also be bound by it.
    """
    import inspect

    from alpha.runtime.events.store.base import RunEventStore

    for method in ("put", "put_if_absent"):
        signature = inspect.signature(getattr(RunEventStore, method))
        annotation = signature.parameters["created_at"].annotation
        text = str(annotation)
        assert "str" in text, f"RunEventStore.{method}(created_at: {text}) no longer declares a string"

    # And no backend widens it to accept a number.
    for backend in (MemoryRunEventStore, JsonlRunEventStore):
        annotation = str(inspect.signature(backend.put).parameters["created_at"].annotation)
        assert "float" not in annotation and "int" not in annotation, f"{backend.__name__}.put widened created_at to {annotation}"


def test_a_float_write_is_rejected_by_the_db_backend(tmp_path):
    """The loudest of the three, and the only one that is actually loud today.

    ``DbRunEventStore.put`` does ``datetime.fromisoformat(created_at)``, so a
    float raises instead of being stored. That is the behaviour the other two
    backends should also have; see the two xfail-marked tests below.
    """
    from alpha.runtime.events.store.db import DbRunEventStore

    async def _init() -> None:
        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'float.db'}", sqlite_dir=str(tmp_path))

    anyio.run(_init)
    try:
        store = DbRunEventStore(get_session_factory())

        async def _write() -> None:
            await store.put(thread_id=THREAD_ID, run_id="r-float", event_type="llm.tool.result", category="trace", created_at=LEGACY_FLOAT)  # type: ignore[arg-type]

        with pytest.raises((TypeError, ValueError)):
            anyio.run(_write)
    finally:
        anyio.run(close_engine)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known defect, reported not fixed (this agent does not own backend/packages/). "
        "MemoryRunEventStore._put_one stores `created_at` verbatim "
        "(packages/harness/alpha/runtime/events/store/memory.py:71), so a legacy epoch float "
        "written by an older release, or by any producer that passes a number, reaches "
        "list_events() as a float and straight onto the wire at GET /runs/{id}/events. "
        "The db backend rejects the same write (fromisoformat) and the run-event contract "
        "declares created_at as a string. Fix: coerce with alpha.utils.time.coerce_iso on the "
        "read path, as app/gateway/routers/projects.py:773 already does for project events."
    ),
)
def test_a_legacy_float_created_at_cannot_reach_a_reader_memory_store():
    """The defect this file reports, for the memory backend.

    A float is exactly the value ``alpha.utils.time.coerce_iso`` exists to heal,
    and exactly the value ``GET /projects/{id}/events`` stopped emitting. The run
    event feed has the same hole and has not been fixed.
    """
    store = MemoryRunEventStore()

    async def _write() -> None:
        await store.put(thread_id=THREAD_ID, run_id="r-legacy", event_type="llm.tool.result", category="trace", created_at=LEGACY_FLOAT)  # type: ignore[arg-type]

    anyio.run(_write)
    row = anyio.run(lambda: store.list_events(THREAD_ID, "r-legacy"))[0]
    _assert_aware_iso(row["created_at"], where="memory/legacy-float")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known defect, reported not fixed (this agent does not own backend/packages/). "
        "JsonlRunEventStore serialises `created_at` verbatim on write "
        "(packages/harness/alpha/runtime/events/events/store/jsonl.py:162, 219, 238) and reads it back with no "
        "coercion, so an event log written by an older release keeps handing floats to every reader. "
        "Same fix as the memory store: coerce on the read path."
    ),
)
def test_a_legacy_float_created_at_cannot_reach_a_reader_jsonl_store(tmp_path):
    """The defect this file reports, for the JSONL backend.

    The on-disk format is the one that outlives a deployment, so a legacy log is
    the realistic way a float gets in — not a hypothetical future producer.
    """
    store = JsonlRunEventStore(tmp_path / "events")

    async def _write() -> None:
        await store.put(thread_id=THREAD_ID, run_id="r-legacy", event_type="llm.tool.result", category="trace", created_at=LEGACY_FLOAT)  # type: ignore[arg-type]

    anyio.run(_write)
    row = anyio.run(lambda: store.list_events(THREAD_ID, "r-legacy"))[0]
    _assert_aware_iso(row["created_at"], where="jsonl/legacy-float")


def test_a_legacy_unix_string_created_at_is_healed_where_a_coercion_exists():
    """The positive counterpart, through the helper every other reader uses.

    ``coerce_iso`` already handles the ``str(time.time())`` shape; this pins that
    it keeps working, so a future change that "simplifies" it is caught here
    rather than by a production reader. The expectation is computed with stdlib
    ``datetime`` rather than a hand-written literal, so it cannot drift into a
    test that is wrong for an unrelated reason.
    """
    from alpha.utils.time import coerce_iso

    expected = datetime.fromtimestamp(LEGACY_FLOAT, UTC).isoformat()
    assert coerce_iso("1756000000.123") == expected, "the legacy unix-seconds string is no longer healed"
    assert coerce_iso(LEGACY_FLOAT) == expected, "a legacy epoch float is no longer healed"
    assert coerce_iso(datetime(2026, 1, 1, 12, 0, 0)) == "2026-01-01T12:00:00+00:00"
    # An ISO string is passed through untouched, never double-coerced.
    iso = "2026-09-27T17:09:23.798611+00:00"
    assert coerce_iso(iso) == iso
    # And a value it cannot interpret is stringified rather than dropped, so a
    # reader still sees *something*.
    assert isinstance(coerce_iso("not a timestamp"), str)
