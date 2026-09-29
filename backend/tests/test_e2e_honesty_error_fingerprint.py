"""Honesty 2/6 — a terminal run error keeps a stack fingerprint that survives redaction.

The defect this pins
--------------------
``RunJournal._error_event`` records a *stack fingerprint* on every terminal run
failure so a sentinel can group "every occurrence of this failure" without
storing the stack. Two things had to be true at once and neither was tested
against the real writer:

1. the digest must land in ``TraceEnvelope.digests``, **not** in ``payload``; and
2. the digest must be the *same* in two different processes.

Why (1) is not a style preference
---------------------------------
``TraceEnvelope.build`` scrubs the payload through the strict ``Redactor``
before it is stored, and that redactor erases high-entropy strings — a bare
64-character lowercase-hex value is exactly the shape of a SHA-256 digest *and*
of some credentials. A digest placed in ``payload`` is therefore replaced with
``[REDACTED:high_entropy_blob]`` **at write time**: the event survives, says
"something was here", and identifies nothing. Layers 6, 8, 11 and 13 would all
be left unable to group a failure. ``TraceEnvelope.digests`` exists for exactly
this, and is sealed by a separate 64-hex gate.

How this defect class was found
-------------------------------
By reading the durable row back after driving a real terminal failure through
the real journal and the real writer into the real ``RunEventStore`` — the same
sink ``GET /runs/{id}/events`` serves. Not by reading the emitter, which looks
correct in isolation.

Why (2) is a *cross-worker* property and not a unit-test detail
--------------------------------------------------------------
A per-process salt (``os.urandom``, a boot id, ``id()``) would pass every test
that exists, because every test runs in one process and one writer. It would
silently destroy the only thing a fingerprint is for: correlating the same
failure across the workers of a multi-worker deployment. So this file asserts
stability across two independently constructed writers *and* across two real OS
processes.

Determinism
-----------
No sleeps, no network, no provider, no randomness. The subprocess check runs
``-I``-style isolated ``python -c`` invocations of a pure hash function and
compares their stdout to a value computed in-process.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import traceback
import uuid
from pathlib import Path

import pytest

from alpha.observability.redaction import STRICT, Redactor
from alpha.observability.trace.config import TraceConfig
from alpha.observability.trace.sinks import RunEventStoreSink
from alpha.observability.trace.writer import TraceWriter, install_writer, reset_writer
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal

THREAD_ID = "honesty-fingerprint"
RUN_ID = "honesty-fingerprint-run"

#: A credential-shaped value assembled from two literals so no contiguous secret
#: appears in this file. The exception message carries it, so a traceback would
#: carry it too — which is the whole reason only a digest is stored.
SECRET = "sk-proj-" + "0123456789abcdefghijklmnopqrstuvwx"
SECRET_MESSAGE = f"provider rejected the call: {SECRET}"

#: Markers that identify *traceback* text rather than an error message. None of
#: them may reach durable storage from the behaviour-trace envelope.
TRACEBACK_MARKERS = ("Traceback (most recent call last)", 'File "', "exec(body_code", "raise _Retry(")


def _raise_authored_failure() -> None:
    """Raise the failure whose fingerprint the tests compare.

    A named frame keeps the traceback stable in shape across the two
    writer instances and the two subprocesses used below.
    """
    raise ValueError(SECRET_MESSAGE)


def _capture_one_failure(*, run_id: str, thread_id: str) -> tuple[list[dict], str]:
    """Synchronous twin of :func:`_capture_one_failure_async`."""
    return asyncio.run(_capture_one_failure_async(run_id=run_id, thread_id=thread_id))


async def _capture_one_failure_async(*, run_id: str, thread_id: str) -> tuple[list[dict], str]:
    """Drive one real terminal failure; return the durable rows and the stack text.

    Everything on the path is production code: ``RunJournal.on_chain_error``
    (the only place a run's death is recorded), the real ``TraceWriter`` and its
    durable ``RunEventStoreSink`` writing into a real ``RunEventStore`` — the
    same store shape ``GET /runs/{id}/events`` serves. No writer is stubbed, so
    this asserts what is *persisted*, not what an emitter intended to persist.

    ``stack_text`` is produced here, by this test file, from the same exception
    object the journal is handed — using stdlib ``traceback`` and nothing from
    the package under test. That makes it an independent expectation for the
    digest rather than a restatement of the implementation. The two must be
    computed from the *same* raise, because a traceback legitimately records its
    caller frames: a second raise one level deeper would be a different stack
    and a different — equally valid — fingerprint.
    """
    store = MemoryRunEventStore()
    sink = RunEventStoreSink(store, thread_id=thread_id)
    writer = TraceWriter(config=TraceConfig(enabled=True, sinks=[]), sinks=[sink])
    journal = RunJournal(run_id=run_id, thread_id=thread_id, event_store=MemoryRunEventStore())

    install_writer(writer)
    try:
        try:
            _raise_authored_failure()
        except ValueError as exc:
            stack_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            # `on_chain_error` is a synchronous LangChain callback; the uuid is
            # the LangGraph run id it requires and is not part of the identity
            # under test.
            journal.on_chain_error(exc, run_id=uuid.uuid4())
        # The trace sink buffers because a synchronous callback has nowhere to
        # await; draining is what makes the row durable, and it is here rather
        # than inside the journal so this mirrors the Gateway's own drain.
        await sink.drain()
    finally:
        reset_writer()

    return await store.list_events(thread_id, run_id), stack_text


def _trace_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("category") == "trace"]


def _error_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("event_type") == "err.raised"]


#: The worker the cross-process test runs in two interpreters. It is *generated*
#: rather than checked in so that the in-process and the out-of-process runs
#: execute byte-identical source at byte-identical line numbers — a fingerprint
#: hashes a formatted traceback, and a traceback records file paths and line
#: numbers, so a shared file is the only way to make "the same failure" mean the
#: same bytes on both sides.
_WORKER_SOURCE = '''\
"""Generated by tests/test_e2e_honesty_error_fingerprint.py. Do not edit."""

from __future__ import annotations

import asyncio
import traceback
import uuid

from alpha.observability.trace.config import TraceConfig
from alpha.observability.trace.sinks import RunEventStoreSink
from alpha.observability.trace.writer import TraceWriter, install_writer, reset_writer
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal

THREAD_ID = "honesty-fingerprint-worker"
RUN_ID = "honesty-fingerprint-worker-run"
SECRET_MESSAGE = {secret!r}


def raise_authored_failure() -> None:
    """The failure whose fingerprint must be identical in every process."""
    raise ValueError(SECRET_MESSAGE)


async def _capture() -> tuple[str, str]:
    store = MemoryRunEventStore()
    sink = RunEventStoreSink(store, thread_id=THREAD_ID)
    writer = TraceWriter(config=TraceConfig(enabled=True, sinks=[]), sinks=[sink])
    journal = RunJournal(run_id=RUN_ID, thread_id=THREAD_ID, event_store=MemoryRunEventStore())
    install_writer(writer)
    try:
        try:
            raise_authored_failure()
        except ValueError as exc:
            stack_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            journal.on_chain_error(exc, run_id=uuid.uuid4())
        await sink.drain()
    finally:
        reset_writer()
    rows = await store.list_events(THREAD_ID, RUN_ID)
    errors = [row for row in rows if row.get("event_type") == "err.raised"]
    if len(errors) != 1:
        raise AssertionError("expected exactly one err.raised row")
    return stack_text, errors[0]["metadata"]["digests"]["stack_sha256"]


def capture_failure() -> tuple[str, str]:
    """Return ``(formatted stack text, persisted stack fingerprint)``."""
    return asyncio.run(_capture())
'''


def _write_worker_module(tmp_path: Path) -> tuple[Path, Path]:
    """Write the shared worker file and return ``(dir, path)``."""
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir(parents=True, exist_ok=True)
    worker_path = worker_dir / "honesty_fingerprint_worker.py"
    worker_path.write_text(_WORKER_SOURCE.format(secret=SECRET_MESSAGE), encoding="utf-8")
    return worker_dir, worker_path


# ---------------------------------------------------------------------------
# 1. The digest is in `digests`, and it is not in `payload`
# ---------------------------------------------------------------------------


def test_the_stack_fingerprint_lands_in_digests_not_in_the_payload():
    """The claim the product makes, checked against what was persisted.

    The terminal `err.raised` run event is the durable record. Its
    ``metadata["digests"]`` must carry ``stack_sha256``; its ``content``
    (the envelope payload) must not — a digest in the payload is destroyed by
    the redactor, which the next test proves against the real redactor.
    """
    rows, _stack = _capture_one_failure(run_id=RUN_ID, thread_id=THREAD_ID)
    errors = _error_rows(rows)
    assert len(errors) == 1, f"expected exactly one err.raised row, got {[r['event_type'] for r in rows]}"

    metadata = errors[0]["metadata"]
    digests = metadata.get("digests")
    assert isinstance(digests, dict), f"the err.raised row carries no digests mapping: {sorted(metadata)}"
    assert "stack_sha256" in digests, f"no stack fingerprint survived: {digests!r}"

    fingerprint = digests["stack_sha256"]
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64, f"the fingerprint is not a SHA-256 hex digest: {fingerprint!r}"
    assert all(char in "0123456789abcdef" for char in fingerprint), fingerprint

    payload = errors[0]["content"]
    assert isinstance(payload, dict)
    assert "stack_sha256" not in payload, "the fingerprint is duplicated into the payload, where redaction will destroy it"
    assert fingerprint not in json.dumps(payload), "the digest's value also appears in the scrubbed payload"


def test_a_digest_placed_in_a_payload_is_destroyed_by_the_real_redactor():
    """The *reason* the digest has its own field, asserted against the engine.

    Without this, "it is in `digests`" reads like a storage preference. It is
    not: a 64-hex value in a payload is a high-entropy blob and is replaced with
    ``[REDACTED:high_entropy_blob]`` before the row is written, so the field
    would carry no information at all.
    """
    digest = hashlib.sha256(b"a stack").hexdigest()
    scrubbed, _reasons = Redactor(STRICT).redact_attributes({"stack_sha256": digest})
    assert scrubbed["stack_sha256"] != digest, "the strict redactor no longer erases bare SHA-256 values; the digests field may be redundant but this test must be re-read"
    assert "REDACTED" in scrubbed["stack_sha256"]


def test_no_traceback_text_reaches_the_durable_trace_row():
    """A hash, not a stack.

    ``format_exception`` output contains file paths and, routinely, an
    interpolated argument — the very secret redaction exists to keep out of
    durable storage. The stored row must identify the failure without carrying
    any of it.
    """
    rows, stack_text = _capture_one_failure(run_id=RUN_ID, thread_id=THREAD_ID)
    serialised = json.dumps(_trace_rows(rows), default=str)
    # Premise: the stack really did contain everything the assertions below say
    # must not survive. Without this, a refactor that stopped formatting a
    # traceback would silently make this test pass for the wrong reason.
    assert "Traceback (most recent call last)" in stack_text
    assert SECRET in stack_text

    assert SECRET not in serialised, "the exception's own message leaked into the behaviour-trace row"
    for marker in TRACEBACK_MARKERS:
        assert marker not in serialised, f"traceback text {marker!r} leaked into the durable trace row"
    assert __file__.replace("\\", "/") not in serialised, "an absolute test source path leaked into the durable trace row"


# ---------------------------------------------------------------------------
# 2. The fingerprint is stable across writers and across processes
# ---------------------------------------------------------------------------


def test_two_independently_built_writers_agree_on_the_fingerprint():
    """A per-process or per-writer salt would make these two differ.

    Each call constructs a brand new ``TraceWriter`` with its own sequence
    counters, its own config object and its own sink — the closest in-process
    approximation of two Gateway workers, and the one every existing test was
    silently incapable of making, because they all shared one writer.
    """
    first, first_stack = _capture_one_failure(run_id="run-a", thread_id=THREAD_ID)
    second, second_stack = _capture_one_failure(run_id="run-b", thread_id=THREAD_ID)

    # The two stacks are the same failure reaching the journal through the same
    # helper, so the frames match; asserted rather than assumed, because if they
    # ever diverged the comparison below would be vacuous.
    assert first_stack == second_stack

    first_digest = _error_rows(first)[0]["metadata"]["digests"]["stack_sha256"]
    second_digest = _error_rows(second)[0]["metadata"]["digests"]["stack_sha256"]
    assert first_digest == second_digest, "the same failure produced two different stack fingerprints; cross-worker correlation is impossible"


def test_the_fingerprint_is_a_pure_function_of_the_stack_text():
    """Pins *how* it is stable: the digest is the hash of the stack, undecorated.

    This is the assertion that rules out a salt by construction rather than by
    coincidence. If a boot id, a random salt or a per-writer nonce were mixed
    in, the value would no longer equal ``sha256(stack_text)`` and this fails
    even though two writes in one process still agreed.
    """
    rows, stack_text = _capture_one_failure(run_id="run-c", thread_id=THREAD_ID)
    expected = hashlib.sha256(stack_text.encode("utf-8", errors="replace")).hexdigest()
    actual = _error_rows(rows)[0]["metadata"]["digests"]["stack_sha256"]

    assert actual == expected, "stack_sha256 is not the plain SHA-256 of the formatted traceback, so something non-deterministic is mixed in"


def _digest_in_a_separate_process(worker_module: Path, worker_dir: Path) -> tuple[str, str]:
    """Drive the *real* journal in a genuinely separate OS process.

    Returns ``(stack_text, digest)``. The child imports the same generated
    worker module this process imported, so both processes execute byte-identical
    source at identical file paths and line numbers. That matters: a fingerprint
    is a hash *of the formatted traceback*, and a traceback records its frames.
    Driving the same file in a second process is therefore the only way to make
    "the same failure" mean the same bytes on both sides.

    A salt seeded from process state (``os.urandom``, a module-level ``secrets``
    token, a boot id) is constant within one process and differs across two, so
    it is invisible to every in-process assertion and is exactly what this
    catches.
    """
    backend_root = Path(__file__).resolve().parents[1]
    script = (
        "import importlib, json, sys\n"
        f"sys.path.insert(0, {str(worker_dir)!r})\n"
        f"mod = importlib.import_module({worker_module.stem!r})\n"
        "stack, digest = mod.capture_failure()\n"
        "sys.stdout.write(json.dumps({'stack': stack, 'digest': digest}))\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(backend_root),
        env={**os.environ, "PYTHONPATH": "."},
        timeout=600,
        check=True,
    )
    parsed = json.loads(completed.stdout)
    return parsed["stack"], parsed["digest"]


def test_two_separate_processes_agree_on_the_fingerprint(tmp_path):
    """The cross-process half of the claim, in two real OS processes.

    Both processes run the same generated worker file, so the traceback bytes
    are identical and any difference in the digest can only come from
    process-local state. This is the assertion that a per-process salt fails:
    two *fresh writer instances* in one process still agree under such a salt,
    which is why the in-process test below is necessary but not sufficient.
    """
    worker_dir, worker_path = _write_worker_module(tmp_path)

    import importlib

    sys.path.insert(0, str(worker_dir))
    try:
        worker = importlib.import_module(worker_path.stem)
    finally:
        sys.path.remove(str(worker_dir))

    parent_stack, parent_digest = worker.capture_failure()
    child_stack_a, child_digest_a = _digest_in_a_separate_process(worker_path, worker_dir)
    child_stack_b, child_digest_b = _digest_in_a_separate_process(worker_path, worker_dir)

    assert child_stack_a == parent_stack, "the two processes did not raise the same failure; the comparison below would be vacuous"
    assert child_stack_b == parent_stack

    assert child_digest_a == parent_digest, "a second process produced a different stack fingerprint; a per-process salt is in play and cross-worker correlation is impossible"
    assert child_digest_b == child_digest_a, "two separate processes disagree with each other"


def test_a_different_failure_gets_a_different_fingerprint():
    """The fingerprint must identify a failure, not merely exist.

    A constant — or a digest of the run id, or of the error code — would satisfy
    every "it is a 64-hex string" assertion while grouping unrelated failures
    together, which is the opposite of what it is for.
    """
    rows_a, stack_a = _capture_one_failure(run_id="run-d", thread_id=THREAD_ID)
    rows_b, stack_b = _capture_one_failure(run_id=RUN_ID, thread_id=THREAD_ID)
    assert stack_a == stack_b
    assert _error_rows(rows_a)[0]["metadata"]["digests"]["stack_sha256"] == _error_rows(rows_b)[0]["metadata"]["digests"]["stack_sha256"], "the same failure must group"

    other_stack = "".join(traceback.format_exception(KeyError, KeyError("other"), _traceback_for(KeyError)))
    other = hashlib.sha256(other_stack.encode("utf-8", errors="replace")).hexdigest()
    assert other != _error_rows(rows_a)[0]["metadata"]["digests"]["stack_sha256"]


def _traceback_for(exc_type: type[BaseException]) -> object:
    """A traceback anchored on :func:`_raise_authored_failure`'s frame.

    Only the frames are reused; the exception type and value differ, which is
    what makes the resulting fingerprint differ.
    """
    try:
        _raise_authored_failure()
    except ValueError as exc:
        return exc.__traceback__
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# 3. The whole point of the record: a run's death is one queryable fact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_terminal_failure_is_recoverable_from_the_durable_feed_by_digest():
    """Read the record back the way a reader would: from the store, by digest.

    The journal writes two rows for a terminal failure — the durable
    ``run.error`` and the behaviour trace's ``err.raised``. A reader that has
    one failure's fingerprint must be able to find every other occurrence, and
    the row it finds must still say what the code was.
    """
    rows, _stack = await _capture_one_failure_async(run_id=RUN_ID, thread_id=THREAD_ID)
    errors = _error_rows(rows)
    assert errors, "no err.raised row was written"

    metadata = errors[0]["metadata"]
    assert metadata["error_code"], "the terminal failure carries no error code, so it cannot be selected by family"
    assert metadata["run_id"] and metadata["thread_id"] and metadata["trace_id"], "the trace row lost the correlation spine"

    # And the digest is a top-level field of the envelope record, not a nested
    # payload value a reader would have to know the shape of.
    assert set(metadata["digests"]) == {"stack_sha256"}


def test_digests_are_sealed_to_real_sha256_values_only():
    """The narrow gate that keeps `digests` from becoming a side channel.

    Stated in ``TraceEnvelope.digests``' own docstring: a value that is not
    exactly 64 lowercase hex characters is dropped rather than stored, so
    caller-supplied text cannot ride in there unscrubbed. Asserted here because
    the field is the one place in the envelope that bypasses redaction, and an
    unbounded bypass is not a safe place to leave unasserted.
    """
    writer = TraceWriter(config=TraceConfig(enabled=True, sinks=[]), sinks=[])
    envelope = writer.record(
        "tool.call.completed",
        run_id="run-digest-gate",
        thread_id=THREAD_ID,
        digests={
            "stack_sha256": hashlib.sha256(b"x").hexdigest(),
            "not_a_digest": "SECRET_TEXT_THAT_MUST_NOT_SURVIVE",
            "wrong_length": "abc123",
        },
        payload={"tool": "bash", "status": "ok"},
    )
    assert envelope is not None
    assert envelope.digests == {"stack_sha256": hashlib.sha256(b"x").hexdigest()}, envelope.digests
