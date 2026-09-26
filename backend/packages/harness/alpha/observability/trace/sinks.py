"""Where sealed envelopes go. Reuse the existing sink protocol; add one durable sink.

The existing :class:`alpha.observability.recorder.TraceSink` protocol is
reused verbatim -- ``name`` / ``emit(record)`` / ``close()`` / ``disclosure()`` --
so :class:`~alpha.observability.recorder.InMemorySink` and
:class:`~alpha.observability.recorder.JsonlFileSink` work here unchanged and
there is one bounding/disclosure idea in the package rather than two.

:class:`RunEventStoreSink` is the addition, and it is the whole point: it writes
to the **durable** :class:`alpha.runtime.events.store.base.RunEventStore` the
Gateway already serves at ``GET /runs/{id}/events``. That store is ``async``,
which is why this sink is not a ``TraceSink`` -- see below.

Async without a running loop
----------------------------
An instrumentation call site is frequently synchronous and frequently not on an
event loop (``BaseCallbackHandler`` methods are sync; a tool may run in a worker
thread). :class:`RunEventStoreSink` therefore keeps a **bounded** buffer of
pending rows plus an optional bound queue, and exposes:

* :meth:`RunEventStoreSink.put_nowait` -- synchronous, non-blocking, never
  raises. The only method a hot path calls.
* :meth:`RunEventStoreSink.drain` -- ``async``, pushes everything pending.
* :meth:`RunEventStoreSink.flush_if_possible` -- schedules ``drain`` on the
  running loop when there is one and does nothing when there is not.

Overflow is counted, never silent, and never retried per event. A store that is
down produces one ``WARNING`` per sink (not per event: a broken store on a hot
path would otherwise turn one outage into a log flood that hides the outage) and
a ``flush_failures`` counter the sentinel can read.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterable
from typing import Any

__all__ = ["RunEventStoreSink", "TraceRecordSink", "rows_for"]

logger = logging.getLogger(__name__)


class TraceRecordSink:
    """Adapter turning a :class:`TraceSink` into a list-of-rows sink.

    The existing record sinks take a ``Mapping``; this adapter normalises the
    payload so the JSONL file, the in-memory ring and the query API all agree on
    what one stored row is.
    """

    __slots__ = ("_inner", "name")

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", type(inner).__name__)

    def emit_envelope(self, envelope: Any) -> None:
        self._inner.emit(envelope.to_record())

    def close(self) -> None:
        self._inner.close()

    def disclosure(self) -> dict[str, int]:
        return dict(self._inner.disclosure())


class RunEventStoreSink:
    """Durable sink backed by the existing ``RunEventStore``.

    Args:
        store: A ``RunEventStore``; anything with ``put_batch(list[dict])`` works.
        name: Stable sink name, used as a disclosure key.
        thread_id: The thread the rows belong to. ``RunEventStore.put_batch``
            takes ``thread_id`` and ``run_id`` **per row**, and this sink is
            handed envelopes rather than rows, so it has to supply them. The
            envelope's own ``thread_id`` wins when it has one; this is the
            fallback for a writer installed once per process. When neither is
            available the row is **refused and counted** under
            ``missing_identity`` rather than written under a fabricated thread --
            a trace row filed against the wrong thread is invisible to the only
            endpoint that serves it, which is the definition of a silent loss.
        max_pending: How many rows may wait for the next drain. Reached means
            :meth:`put_nowait` refuses and counts ``overflowed`` -- the buffer is
            bounded because an unbounded one in a long-lived Gateway is a memory
            leak with a telemetry-shaped name.
        max_pending_batches: How many flush failures to retain per batch before
            dropping the batch. A batch whose write failed is *not* retried
            forever: the sentinel re-derives what it needs from the run's own
            events, and an unbounded retry queue is how an observability outage
            becomes a memory outage.
    """

    __slots__ = (
        "_closed",
        "_failure_lock",
        "_flush_failures",
        "_flush_failures_logged",
        "_lock",
        "_missing_identity",
        "_pending",
        "_store",
        "calls",
        "dropped_events",
        "max_pending",
        "max_pending_batches",
        "name",
        "overflowed",
        "thread_id",
    )

    def __init__(
        self,
        store: Any,
        *,
        name: str = "run_event_store",
        thread_id: str | None = None,
        max_pending: int = 2048,
        max_pending_batches: int = 32,
    ) -> None:
        if not hasattr(store, "put_batch"):
            raise TypeError("RunEventStoreSink needs a store exposing put_batch(list[dict])")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        self._store = store
        self.name = name
        self.thread_id = thread_id
        self.max_pending = max_pending
        self.max_pending_batches = max_pending_batches
        self._pending: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.calls = 0
        self.overflowed = 0
        self.dropped_events = 0
        self._missing_identity = 0
        self._flush_failures: dict[str, int] = {}
        self._flush_failures_logged: set[str] = set()
        self._failure_lock = threading.Lock()
        self._closed = False

    # -- write path (sync, non-blocking, never raises) -------------------------

    def _row_for(self, envelope: Any) -> dict[str, Any] | None:
        """Return the store row for *envelope*, or ``None`` when it has no thread.

        ``thread_id`` and ``run_id`` are the store's own addressing keys, not
        envelope metadata: ``RunEventStore.put_batch`` requires both on every row,
        so a sink that forgot them would turn every single write into a flush
        failure and the disclosure would read "the database is broken" when the
        database is fine.
        """
        row = envelope.to_run_event()
        thread_id = envelope.thread_id or self.thread_id
        if not thread_id:
            return None
        return {"thread_id": thread_id, "run_id": envelope.run_id, **row}

    def put_nowait(self, envelope: Any) -> bool:
        """Queue one envelope's durable row. Returns whether it was queued.

        Never raises. A sink is not allowed to become the reason a run fails, and
        the refusal is counted so "the trace is missing" is a number somebody can
        read rather than a silence.
        """
        if self._closed:
            return False
        try:
            row = self._row_for(envelope)
        except Exception as exc:  # noqa: BLE001 - a bad envelope must not break the run
            self._note_failure("envelope", exc)
            return False
        if row is None:
            self._note_missing_identity(envelope)
            return False
        return self._enqueue(row)

    def _enqueue(self, row: dict[str, Any]) -> bool:
        with self._lock:
            if len(self._pending) >= self.max_pending:
                self.overflowed += 1
                self.dropped_events += 1
                return False
            self._pending.append(row)
            self.calls += 1
        return True

    def _note_missing_identity(self, envelope: Any) -> None:
        self._missing_identity += 1
        self.dropped_events += 1
        self._note_failure(
            "missing_identity",
            RuntimeError(f"envelope {getattr(envelope, 'event_id', '?')} carries no thread_id and this sink has no default; the row was refused rather than filed under a fabricated thread"),
        )

    def emit(self, record: Any) -> None:
        """Satisfy the record-sink shape for callers that iterate sinks generically.

        Accepts an already-built row (``dict``, which must already carry
        ``thread_id``) as well as an envelope, so the adapter and this sink can be
        driven from one loop.
        """
        if not isinstance(record, dict):
            self.put_nowait(record)
            return
        row = record if record.get("thread_id") else {"thread_id": self.thread_id, "run_id": record.get("run_id"), **record}
        if not row.get("thread_id"):
            self._note_missing_identity(record)
            return
        self._enqueue(row)

    def close(self) -> None:
        self._closed = True

    # -- flush path (async) ----------------------------------------------------

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _take_batch(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._pending:
                return []
            batch = self._pending
            self._pending = []
            return batch

    async def drain(self) -> int:
        """Write every pending row. Returns how many were written.

        A failure returns the rows to the *front* of the buffer up to
        ``max_pending_batches`` worth, counts ``flush_failures`` and logs once
        per failure kind. It does not retry inside the call: a caller decides
        when to try again, and an internal retry loop is how a store outage turns
        into a CPU outage.
        """
        batch = self._take_batch()
        if not batch:
            return 0
        try:
            await self._store.put_batch(batch)
        except Exception as exc:  # noqa: BLE001 - the traced run must not fail here
            self._note_failure("flush", exc)
            with self._lock:
                keep = batch[: self.max_pending_batches]
                self._pending = keep + self._pending
            return 0
        return len(batch)

    def flush_if_possible(self) -> bool:
        """Schedule a drain on the running loop, if there is one.

        Returns whether a drain was scheduled. ``False`` means "no loop here, the
        rows are still pending", which is the correct answer from a worker thread
        and from a synchronous callback; the rows are not lost, they wait for the
        next :meth:`drain`.
        """
        if not self.pending:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        loop.create_task(self._guarded_drain())
        return True

    async def _guarded_drain(self) -> None:
        try:
            await self.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a task must not die silently
            self._note_failure("drain", exc)

    def _note_failure(self, kind: str, exc: BaseException) -> None:
        with self._failure_lock:
            self._flush_failures[kind] = self._flush_failures.get(kind, 0) + 1
            first = kind not in self._flush_failures_logged
            if first:
                self._flush_failures_logged.add(kind)
        if first:
            logger.warning("trace sink %r failed to %s and the affected records are lost; further failures of this kind will not be logged individually: %s", self.name, kind, exc)

    # -- disclosure -------------------------------------------------------------

    def disclosure(self) -> dict[str, int]:
        with self._lock:
            pending = len(self._pending)
        with self._failure_lock:
            failures = dict(self._flush_failures)
        return {
            "calls": self.calls,
            "pending": pending,
            "overflowed": self.overflowed,
            "missing_identity": self._missing_identity,
            "dropped_events": self.dropped_events,
            "flush_failures": sum(failures.values()),
            **{f"flush_failures_{kind}": count for kind, count in failures.items()},
        }

    def __repr__(self) -> str:
        return f"RunEventStoreSink(name={self.name!r}, pending={self.pending}, calls={self.calls})"


def rows_for(envelopes: Iterable[Any]) -> list[dict[str, Any]]:
    """Return the durable rows for *envelopes*, in order."""
    return [envelope.to_run_event() for envelope in envelopes]
