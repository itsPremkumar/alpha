"""A run that errored must never reach the client as a success-looking ``end``.

``app.gateway.services.sse_consumer`` used to close every stream with
``event: end\\ndata: null``, whatever the run's real outcome. Because
``publish_end`` is the *last* thing a worker does -- after the artifact
delivery gate and the delivery-receipt write have had their chance to flip the
status -- that bare frame is exactly what an artifact-delivery failure looked
like to a client: a completed run.

These tests pin the fixed contract:

* the ``end`` frame carries the run's real status (``status``/``ok``), and
* a run that reached ``RunStatus.error`` without publishing an ``error`` frame
  of its own gets one synthesized ahead of ``end``, with a stable code and the
  run's correlation/trace ids,
* while a genuinely successful run still gets a plain ``end`` and no
  synthesized ``error`` frame, and
* a producer-published ``error`` frame is passed through and never duplicated.

The ``store_only`` cases matter on their own: a cross-worker join hydrates its
``RunRecord`` once, when the client connects, so the local copy still says
``running`` when END arrives. Reporting from that stale copy is the same defect
in a different costume.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from alpha.runtime import ORPHAN_RECOVERY_STOP_REASON, RunManager, RunRecord, RunStatus
from alpha.runtime.runs.schemas import DisconnectMode
from alpha.runtime.runs.store.memory import MemoryRunStore
from alpha.runtime.stream_bridge.memory import MemoryStreamBridge

THREAD_ID = "thread-terminal-status"


class _Request:
    """Minimal never-disconnected stand-in for a FastAPI ``Request``."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


def _frame_payload(frame: str) -> Any:
    """Decode the ``data:`` line of one SSE frame."""
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise AssertionError(f"frame has no data line: {frame!r}")


def _events(frames: list[str]) -> list[str]:
    return [frame.split("\n", 1)[0].removeprefix("event: ") for frame in frames]


def _frame_for(frames: list[str], event: str) -> str:
    matches = [frame for frame in frames if frame.startswith(f"event: {event}\n")]
    assert len(matches) == 1, f"expected exactly one {event!r} frame, got {_events(frames)}"
    return matches[0]


def _record(
    *,
    run_id: str = "run-terminal",
    status: RunStatus = RunStatus.running,
    store_only: bool = False,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
    stop_reason: str | None = None,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        thread_id=THREAD_ID,
        assistant_id=None,
        status=status,
        on_disconnect=DisconnectMode.continue_,
        store_only=store_only,
        metadata=metadata or {},
        error=error,
        stop_reason=stop_reason,
    )


@pytest.mark.anyio
async def test_errored_run_end_frame_reports_error_status_not_success():
    """The defect, verbatim: finalization flips the run to error, the producer
    publishes no ``error`` frame, and the client used to see a bare ``end``."""
    from app.gateway.services import GATEWAY_TERMINAL_ERROR_CODE, sse_consumer

    async def run() -> None:
        run_manager = RunManager(store=MemoryRunStore())
        bridge = MemoryStreamBridge()
        run_record = await run_manager.create(thread_id=THREAD_ID, on_disconnect=DisconnectMode.continue_)
        await run_manager.set_status(run_record.run_id, RunStatus.running)
        await bridge.publish(run_record.run_id, "values", {"step": 1})

        # What the worker's delivery gate does on a real artifact-delivery
        # failure: set the terminal status, publish nothing else, then END.
        await run_manager.set_status(
            run_record.run_id,
            RunStatus.error,
            error="Artifact delivery incomplete: no produced output artifact was presented",
        )
        await bridge.publish_end(run_record.run_id)

        consumer = sse_consumer(bridge, run_record, _Request(), run_manager)
        frames = [frame async for frame in consumer]

        assert _events(frames)[-1] == "end"
        end_payload = _frame_payload(_frame_for(frames, "end"))
        assert end_payload["status"] == RunStatus.error.value
        assert end_payload["ok"] is False
        assert end_payload["error"] == "Artifact delivery incomplete: no produced output artifact was presented"
        assert end_payload["run_id"] == run_record.run_id
        assert end_payload["thread_id"] == THREAD_ID

        error_payload = _frame_payload(_frame_for(frames, "error"))
        assert error_payload["code"] == GATEWAY_TERMINAL_ERROR_CODE
        assert error_payload["status"] == RunStatus.error.value
        assert error_payload["message"] == "Artifact delivery incomplete: no produced output artifact was presented"
        assert error_payload["run_id"] == run_record.run_id
        assert error_payload["correlation_id"] == run_record.run_id
        assert error_payload["trace_id"]

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_errored_run_emits_error_frame_before_end():
    """Ordering is part of the contract: a client that stops reading on the
    first terminal frame must still see the failure, not the ``end``."""
    from app.gateway.services import sse_consumer

    async def run() -> None:
        run_manager = RunManager()
        bridge = MemoryStreamBridge()
        record = _record(status=RunStatus.error, error="lease expired")
        await bridge.publish_end(record.run_id)

        frames = [frame async for frame in sse_consumer(bridge, record, _Request(), run_manager)]

        assert _events(frames) == ["error", "end"]

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_successful_run_end_frame_reports_success_and_no_error_frame():
    """The inverse guard: the fix must not turn every run into a failure."""
    from app.gateway.services import sse_consumer

    async def run() -> None:
        run_manager = RunManager()
        bridge = MemoryStreamBridge()
        record = _record(status=RunStatus.success)
        await bridge.publish_end(record.run_id)

        frames = [frame async for frame in sse_consumer(bridge, record, _Request(), run_manager)]

        assert _events(frames) == ["end"]
        end_payload = _frame_payload(_frame_for(frames, "end"))
        assert end_payload["status"] == RunStatus.success.value
        assert end_payload["ok"] is True
        assert "error" not in end_payload
        assert "stop_reason" not in end_payload

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_interrupted_run_is_terminal_but_not_reported_as_a_failure():
    """``interrupted`` is terminal without being a server-side failure, so the
    ``end`` frame names it and no ``error`` frame is invented."""
    from app.gateway.services import sse_consumer

    async def run() -> None:
        run_manager = RunManager()
        bridge = MemoryStreamBridge()
        record = _record(status=RunStatus.interrupted, stop_reason="client_cancelled")
        await bridge.publish_end(record.run_id)

        frames = [frame async for frame in sse_consumer(bridge, record, _Request(), run_manager)]

        assert _events(frames) == ["end"]
        end_payload = _frame_payload(_frame_for(frames, "end"))
        assert end_payload["status"] == RunStatus.interrupted.value
        assert end_payload["ok"] is False
        assert end_payload["stop_reason"] == "client_cancelled"

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_producer_error_frame_is_passed_through_and_never_duplicated():
    """A worker's own terminal ``error`` frame already carries a specific code;
    the Gateway must not answer a second failure frame for the same run."""
    from app.gateway.services import sse_consumer

    producer_payload = {
        "code": "delivery_incomplete",
        "message": "Artifact delivery incomplete",
        "status": RunStatus.error.value,
        "run_id": "run-terminal",
        "thread_id": THREAD_ID,
        "correlation_id": "run-terminal",
        "trace_id": "trace-from-worker",
    }

    async def run() -> None:
        run_manager = RunManager()
        bridge = MemoryStreamBridge()
        record = _record(status=RunStatus.error, error="Artifact delivery incomplete")
        await bridge.publish(record.run_id, "error", producer_payload)
        await bridge.publish_end(record.run_id)

        frames = [frame async for frame in sse_consumer(bridge, record, _Request(), run_manager)]

        assert _events(frames) == ["error", "end"]
        # Byte-for-byte the producer's frame: its code and trace id are the ones
        # the client correlates on.
        assert _frame_payload(_frame_for(frames, "error")) == producer_payload
        end_payload = _frame_payload(_frame_for(frames, "end"))
        assert end_payload["status"] == RunStatus.error.value
        assert end_payload["ok"] is False

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_store_only_join_reports_the_durable_error_status_not_the_stale_running_copy():
    """A cross-worker join hydrates its record once. END must not be rendered
    from the ``running`` snapshot taken when the client connected."""
    from app.gateway.services import GATEWAY_TERMINAL_ERROR_CODE, sse_consumer

    async def run() -> None:
        store = MemoryRunStore()
        await store.put("run-store-only", thread_id=THREAD_ID, status="running")
        run_manager = RunManager(store=store)
        stale = await run_manager.get("run-store-only")
        assert stale is not None
        assert stale.store_only is True
        assert stale.status == RunStatus.running

        bridge = MemoryStreamBridge()
        await bridge.publish("run-store-only", "values", {"step": 1})
        # Finalization lands in the durable row, and END follows, while this
        # consumer is still holding the record hydrated at ``running``.
        await store.update_status("run-store-only", "error", error="delivery receipt could not be persisted")
        await bridge.publish_end("run-store-only")

        frames = [frame async for frame in sse_consumer(bridge, stale, _Request(), run_manager)]

        end_payload = _frame_payload(_frame_for(frames, "end"))
        assert end_payload["status"] == RunStatus.error.value
        assert end_payload["ok"] is False
        assert end_payload["error"] == "delivery receipt could not be persisted"
        assert _frame_payload(_frame_for(frames, "error"))["code"] == GATEWAY_TERMINAL_ERROR_CODE

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_store_only_join_after_orphan_recovery_reports_the_recovered_outcome():
    """The heartbeat liveness boundary synthesizes END; the synthesized frame
    must still name the recovered outcome rather than ``data: null``."""
    from app.gateway.services import GATEWAY_TERMINAL_ERROR_CODE, sse_consumer

    async def run() -> None:
        store = MemoryRunStore()
        await store.put("run-orphan", thread_id=THREAD_ID, status="running")
        run_manager = RunManager(store=store)
        record = await run_manager.get("run-orphan")
        assert record is not None
        bridge = MemoryStreamBridge(heartbeat_interval=0.01)
        await bridge.publish("run-orphan", "values", {"step": 1})
        consumer = sse_consumer(bridge, record, _Request(), run_manager)
        first = await anext(consumer)
        assert first.startswith("event: values\n")

        await store.update_status(
            "run-orphan",
            "error",
            error="lease expired",
            stop_reason=ORPHAN_RECOVERY_STOP_REASON,
        )

        tail = [frame async for frame in consumer]
        assert _events(tail) == ["error", "end"]
        assert _frame_payload(_frame_for(tail, "error"))["code"] == GATEWAY_TERMINAL_ERROR_CODE
        end_payload = _frame_payload(_frame_for(tail, "end"))
        assert end_payload["status"] == RunStatus.error.value
        assert end_payload["stop_reason"] == ORPHAN_RECOVERY_STOP_REASON

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_synthesized_error_frame_correlates_with_the_run_metadata_trace_id():
    """The trace id is the one the run record was stamped with at admission, so
    the frame joins to the run's logs instead of minting an unrelated id."""
    from alpha.trace_context import AGENT_WORKSPACE_TRACE_METADATA_KEY
    from app.gateway.services import sse_consumer

    async def run() -> None:
        run_manager = RunManager()
        bridge = MemoryStreamBridge()
        record = _record(
            status=RunStatus.error,
            error="workspace snapshot failed",
            metadata={AGENT_WORKSPACE_TRACE_METADATA_KEY: "trace-stamped-at-admission"},
        )
        await bridge.publish_end(record.run_id)

        frames = [frame async for frame in sse_consumer(bridge, record, _Request(), run_manager)]

        assert _frame_payload(_frame_for(frames, "error"))["trace_id"] == "trace-stamped-at-admission"

    await asyncio.wait_for(run(), timeout=10.0)


@pytest.mark.anyio
async def test_end_frame_error_payload_is_used_when_no_error_text_was_recorded():
    """A status flip with no recorded message still has to produce a message a
    human can act on rather than a null."""
    from app.gateway.services import sse_consumer

    async def run() -> None:
        run_manager = RunManager()
        bridge = MemoryStreamBridge()
        record = _record(status=RunStatus.error)
        await bridge.publish_end(record.run_id)

        frames = [frame async for frame in sse_consumer(bridge, record, _Request(), run_manager)]

        message = _frame_payload(_frame_for(frames, "error"))["message"]
        assert RunStatus.error.value in message

    await asyncio.wait_for(run(), timeout=10.0)


def test_end_frame_payload_names_the_run_when_the_stream_is_already_gone():
    """A terminal run whose stream was cleaned up still reports its status."""
    from app.gateway.services import sse_consumer

    class _MissingStreamBridge(MemoryStreamBridge):
        async def stream_exists(self, run_id: str) -> bool:
            return False

    async def run() -> None:
        run_manager = RunManager()
        record = _record(
            run_id="run-no-stream",
            status=RunStatus.error,
            error="artifact delivery incomplete",
            store_only=True,
        )
        frames = [frame async for frame in sse_consumer(_MissingStreamBridge(), record, _Request(), run_manager)]

        assert _events(frames) == ["error", "end"]
        end_payload = _frame_payload(_frame_for(frames, "end"))
        assert end_payload["run_id"] == "run-no-stream"
        assert end_payload["status"] == RunStatus.error.value
        assert end_payload["ok"] is False

    asyncio.run(run())


def test_terminal_end_payload_is_json_serializable_for_a_bare_record():
    """``format_sse`` runs ``json.dumps(..., default=str)``; a terminal payload
    built from a minimally populated record must not surprise it."""
    from app.gateway.services import terminal_end_payload

    payload = terminal_end_payload(
        SimpleNamespace(run_id="r", thread_id="t", status=RunStatus.success, error=None, stop_reason=None)  # type: ignore[arg-type]
    )

    assert json.loads(json.dumps(payload, default=str)) == {
        "run_id": "r",
        "thread_id": "t",
        "status": "success",
        "ok": True,
    }
