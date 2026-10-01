"""``stack_sha256`` is a hash of the real stack, and it is stable across processes.

The terminal run error is stamped with a fingerprint whose entire purpose is to
make "these two failures are the same failure" a *decidable* question rather than a
resemblance a reader has to judge. Three properties have to hold for that to be
worth anything:

1. It is a real SHA-256 of real bytes -- not a constant, a placeholder, or a
   per-process random value.
2. It is **deterministic**: the same failure digested twice in one process agrees.
3. It is **stable across processes**: the same failure digested in two separate
   interpreters agrees.

(3) is the property that actually rules out a per-process salt -- ``uuid4()``,
``id()``, ``os.getpid()``, a clock, a memory address -- and **no in-process test can
see it**, because every such value is constant within one process. That is the gap
this file exists to close: ``RunJournal._error_event`` documents cross-process
stability, and before this file nothing checked it.

Each child is a real, separate interpreter that imports the production journal and
raises the failure for real, so what comes back is the shipped computation and not a
re-implementation of the hashing in the test. A re-implementation would keep passing
after the journal's hashing changed, which is precisely the regression worth
catching.

Also pinned here: the digest lives in
:attr:`~alpha.observability.trace.contract.TraceEnvelope.digests` and is readable
back out of the durable run-event row, so "a deduplicated trace can be proven to be
the same failure" survives the write. Correlation that only worked in memory would
not be correlation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from alpha.observability.trace.config import TraceConfig
from alpha.observability.trace.contract import TraceEnvelope
from alpha.observability.trace.writer import TraceWriter, install_writer, reset_writer
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal

BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: One child program, parameterised by the failure message, so the "same failure"
#: and "a different failure" cases differ by exactly the one thing under test.
_CHILD_TEMPLATE = """
import asyncio, json, traceback
from uuid import uuid4

from alpha.observability.trace.config import TraceConfig
from alpha.observability.trace.contract import TraceEnvelope
from alpha.observability.trace.writer import TraceWriter, install_writer
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal

MESSAGE = {message!r}


def inner():
    raise RuntimeError(MESSAGE)


def outer():
    inner()


store = MemoryRunEventStore()
writer = TraceWriter(TraceConfig(enabled=True, sinks=["run_events"]), store=store, thread_id="t-1")
install_writer(writer)
journal = RunJournal("r-1", "t-1", store, flush_threshold=10000)
# The formatted stack is captured *inside* the except block on purpose: Python
# unbinds the ``as ... as`` name when the block exits, so reading it afterwards
# would raise NameError and take the whole child down.
formatted = ""
try:
    outer()
except RuntimeError as exc:
    journal.on_chain_error(exc, run_id=uuid4())
    formatted = "".join(traceback.format_exception(RuntimeError, exc, exc.__traceback__))

asyncio.run(writer.drain())
rows = []
for thread_events in store._events.values():
    for row in thread_events:
        if row.get("metadata", {{}}).get("schema_version") == 1:
            rows.append(row)
raised = [r for r in rows if r["event_type"] == "err.raised"]
print(json.dumps({{
    "digests": [TraceEnvelope.from_run_event(r).digests for r in raised],
    "formatted": formatted,
}}))
"""

SAME_FAILURE = "graph blew up"
OTHER_FAILURE = "graph blew up, differently"


def _run_child(message: str) -> dict:
    """Run the production journal in a separate interpreter and return its findings."""
    env = dict(os.environ)
    # The child is a bare interpreter, so it needs the same import path the suite
    # runs with; inheriting a relative PYTHONPATH would resolve against whatever
    # directory the child happens to start in.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", _CHILD_TEMPLATE.format(message=message)],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    payload = json.loads(completed.stdout)
    assert len(payload["digests"]) == 1, f"expected exactly one terminal error event, got {payload['digests']}"
    return payload


@lru_cache(maxsize=4)
def _child(message: str) -> tuple[str, str]:
    """Return ``(stack_sha256, formatted_stack)`` as the *production journal* computed them."""
    payload = _run_child(message)
    return payload["digests"][0]["stack_sha256"], payload["formatted"]


def _child_digest(message: str = SAME_FAILURE) -> str:
    return _child(message)[0]


def test_the_stack_fingerprint_is_stable_across_separate_processes():
    """The property no in-process test can observe.

    Two fresh interpreters, each raising the same failure down the same call path,
    must agree on the same 64 hex characters. If the journal ever salted the digest
    with anything process-specific, this is the test that fails.
    """
    first = _run_child(SAME_FAILURE)["digests"][0]["stack_sha256"]
    second = _run_child(SAME_FAILURE)["digests"][0]["stack_sha256"]

    assert first == second, "the same failure must digest identically in two separate processes"
    assert len(first) == 64
    assert all(character in "0123456789abcdef" for character in first), "lowercase hex, the shape the digest gate accepts"


def test_the_stack_fingerprint_is_the_hash_of_the_real_stack():
    """It hashes the actual formatted traceback, byte for byte.

    ``traceback.format_exception`` output is what the journal digests, so this
    recomputes it from the child's own reported stack and compares. A constant, a
    truncated stack, or a hash of only the exception message would all fail here.
    """
    digest, formatted = _child(SAME_FAILURE)
    expected = hashlib.sha256(formatted.encode("utf-8", errors="replace")).hexdigest()

    assert digest == expected
    # The premise of the assertion above: this really is a multi-frame stack, so the
    # digest covers more than the final "RuntimeError: graph blew up" line.
    assert formatted.count("line ") >= 2, "two frames of the real stack, not just the exception line"
    assert f"RuntimeError: {SAME_FAILURE}" in formatted


def test_two_different_failures_do_not_share_a_fingerprint():
    """A fingerprint that cannot tell two failures apart is not a fingerprint.

    Same exception type and the same call path, one differing word in the message.
    Correlation is the entire use of this field, so a collision here would quietly
    disable it while every individual assertion above kept passing.
    """
    assert _child_digest(OTHER_FAILURE) != _child_digest(SAME_FAILURE)


def test_the_fingerprint_survives_the_durable_row_so_a_read_back_event_can_be_matched():
    """Correlation has to work on what was *stored*, not only on what was built.

    The envelope round-trips through the run-event store -- ``to_run_event`` keeps
    everything except the payload in ``metadata`` -- and ``digests`` is part of that.
    If it were dropped on write, a later reader could see "this run failed" but could
    never match the failure against another one, which is the whole point.
    """
    store = MemoryRunEventStore()
    writer = TraceWriter(TraceConfig(enabled=True, sinks=["run_events"]), store=store, thread_id="t-1")
    install_writer(writer)
    try:
        journal = RunJournal("r-1", "t-1", store, flush_threshold=10_000)
        journal.on_chain_error(RuntimeError(SAME_FAILURE), run_id=uuid4())
        asyncio.run(writer.drain())  # the sink buffers until a drain; the reader cannot see it before

        raised_rows = [row for row in _persisted_rows(store) if row["event_type"] == "err.raised"]
        assert len(raised_rows) == 1
        assert "stack_sha256" in raised_rows[0]["metadata"]["digests"], "the digest must be durable, not in-memory only"

        # Read it back through the public store API a Gateway route would use.
        read_back = asyncio.run(store.list_events("t-1", "r-1", event_types=["err.raised"]))
        assert len(read_back) == 1
        envelope = TraceEnvelope.from_run_event(read_back[0])
        assert len(envelope.digests["stack_sha256"]) == 64
        assert "Traceback" not in json.dumps(read_back[0]), "and the stack itself is still not stored"
    finally:
        reset_writer()


def _persisted_rows(store: MemoryRunEventStore) -> list[dict]:
    """The trace rows the store holds, using the journal's own ``schema_version`` marker."""
    rows: list[dict] = []
    for thread_events in store._events.values():  # noqa: SLF001 - the journal's own store, read in a test
        for row in thread_events:
            if row.get("metadata", {}).get("schema_version") == 1:
                rows.append(row)
    return rows


def test_the_digest_gate_rejects_a_non_digest_in_the_digests_mapping() -> None:
    """``digests`` is gated to real SHA-256 hex, so it cannot become a text side channel.

    This is the property that makes putting the fingerprint there safe, and it is
    the reason the emitters may write there at all: caller-supplied text that merely
    *looks* opaque is dropped rather than stored. Pinned because the alternative
    reading of "the hash is not in the payload, so put it in digests" is "digests is
    an unvalidated bucket", which is exactly what it is not.
    """
    envelope = TraceEnvelope.build(
        event_type="err.raised",
        run_id="a1b2c3d4e5f60718293a4b5c6d7e8f90",
        trace_id="trace-1",
        seq=1,
        payload={"error_code": "RUN_EXECUTION_FAILED", "message": "x", "retried": False},
        digests={"stack_sha256": "not-a-digest", "prompt_sha256": hashlib.sha256(b"p").hexdigest()},
    )
    assert "stack_sha256" not in envelope.digests, "text that is not a SHA-256 is dropped, not stored"
    assert len(envelope.digests["prompt_sha256"]) == 64
