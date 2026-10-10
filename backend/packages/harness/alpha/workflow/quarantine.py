"""Dead-letter quarantine for workflow nodes that ran out of road.

Why this exists
---------------
A node whose retries are exhausted ends ``FAILED``, the run fails closed, and
the run eventually leaves memory. What survives is the append-only event log —
which is authoritative but not *actionable*: an operator who wants to re-drive
that one node has to read a JSONL journal, find the failure among hundreds of
events, reconstruct the run, and hand-build a patch. In practice that means
dead nodes are abandoned rather than recovered.

This module is the missing queue between "the journal recorded it" and "a
human re-ran it". A record is written at the exact moment the engine decides a
node is out of road — retries exhausted, stagnation detected, or a retry
refused as provably futile — and it holds what an operator needs to act: the
real reason, the failure class, the normalized error signature, and the
attempt count.

What it deliberately is not
---------------------------
* **Not an executor.** Nothing here runs a node, resumes a run, or fabricates
  a success. Replay is a real typed patch applied through the engine's own
  ``apply_patch`` with its own optimistic-concurrency check; if the source run
  is no longer resident the replay is refused with the real reason.
* **Not a second status system.** A quarantined node is still a FAILED node in
  the run. The record is a work-queue entry that *points at* that failure; it
  never rewrites it, and a discarded record does not un-fail anything.
* **Not shared state.** The store is the same single-Gateway atomically
  replaced JSON file as the lease and trigger stores beside it: atomic and
  restart-recoverable for ONE Gateway process, never cross-process
  coordination.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "QuarantineRecord",
    "QuarantineStatus",
    "QuarantineStore",
    "QuarantineStoreError",
    "QuarantineTrigger",
]


class QuarantineStoreError(RuntimeError):
    """The quarantine store could not be read or written (fail-closed, never silent)."""


class QuarantineStatus(StrEnum):
    """Where a dead node sits in the operator work queue."""

    QUARANTINED = "quarantined"
    REPLAYED = "replayed"
    DISCARDED = "discarded"


class QuarantineTrigger(StrEnum):
    """Which engine decision put the node here.

    Recorded verbatim from the emitting seam so the queue shows *why* the node
    was dead-lettered, not just that it was.
    """

    RECOVERY_EXHAUSTED = "recovery_exhausted"
    STAGNATION = "stagnation"
    RETRY_REFUSED = "retry_refused"
    BUDGET_EXHAUSTED = "budget_exhausted"


class QuarantineRecord(BaseModel):
    """One dead node awaiting an operator decision."""

    record_id: str
    run_id: str
    workflow_id: str
    owner_id: str | None = None
    node_id: str
    #: The node's own real failure text, carried through verbatim.
    reason: str
    failure_class: str = "unknown"
    #: Normalized signature in which identifiers/numbers/paths/digests collapse,
    #: so "the same fault wearing different digits" groups as one record.
    signature: str = ""
    trigger: QuarantineTrigger = QuarantineTrigger.RECOVERY_EXHAUSTED
    attempts: int = 1
    graph_version: int = 0
    status: QuarantineStatus = QuarantineStatus.QUARANTINED
    quarantined_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    #: Operator-facing resolution, never inferred.
    resolution_note: str = ""
    #: What the replay actually did: the new graph version of the SAME run, or
    #: the real reason it was refused. A record never claims a run it did not
    # produce.
    replay_graph_version: int | None = None
    replay_refusal: str = ""
    discarded_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class QuarantineStore:
    """Durable dead-letter registry, atomically replaced on every mutation.

    Construct with ``store_dir`` to persist (the engine does); without it the
    store is process-local, which is what a throwaway dry run wants. An
    unreadable or corrupt file raises :class:`QuarantineStoreError` rather than
    answering as an empty queue: a dead node hidden by an unreadable store is
    the exact silent failure this module exists to prevent.
    """

    def __init__(self, store_dir: Path | None = None, *, store_name: str = "quarantine.json") -> None:
        self._path = (Path(store_dir) / store_name) if store_dir is not None else None
        self._records: dict[str, QuarantineRecord] = {}
        if self._path is not None:
            self._load()

    @property
    def store_path(self) -> Path | None:
        return self._path

    def _load(self) -> None:
        path = self._path
        if path is None:
            return
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise QuarantineStoreError(f"failed to read quarantine store {path}: {exc}") from exc
        if not raw.strip():
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise QuarantineStoreError(f"quarantine store {path} is not valid JSON: {exc}") from exc
        version = payload.get("schema_version")
        if version != 1:
            raise QuarantineStoreError(f"quarantine store {path} has unsupported schema_version {version!r} (expected 1)")
        for key, entry in (payload.get("records") or {}).items():
            try:
                self._records[key] = QuarantineRecord.model_validate(entry)
            except Exception as exc:  # noqa: BLE001 - a malformed record is a store error, not a crash
                raise QuarantineStoreError(f"quarantine store {path} has a malformed record for {key!r}: {exc}") from exc

    def _persist(self) -> None:
        path = self._path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        try:
            with _PERSIST_LOCK:
                payload = {
                    "schema_version": 1,
                    "records": {key: record.to_dict() for key, record in self._records.items()},
                }
                tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise QuarantineStoreError(f"failed to persist quarantine store {path}: {exc}") from exc

    # ------------------------------------------------------------------ writes

    def admit(
        self,
        *,
        run_id: str,
        workflow_id: str,
        node_id: str,
        reason: str,
        trigger: QuarantineTrigger,
        owner_id: str | None = None,
        failure_class: str = "unknown",
        signature: str = "",
        attempts: int = 1,
        graph_version: int = 0,
    ) -> QuarantineRecord:
        """Dead-letter one node, or refresh the record that already holds it.

        One open record per ``(run_id, node_id)``: a node that fails, is
        replayed, and fails again is a NEW event in the queue rather than an
        edit of the one an operator already read. A still-open record is
        updated in place with the newer reason/attempts, because the operator
        has not acted on it yet and two rows for one undecided node would read
        as two decisions to make.
        """
        with _PERSIST_LOCK:
            for record in self._records.values():
                if record.run_id == run_id and record.node_id == node_id and record.status is QuarantineStatus.QUARANTINED:
                    record.reason = reason
                    record.failure_class = failure_class
                    record.signature = signature
                    record.trigger = trigger
                    record.attempts = attempts
                    record.graph_version = graph_version
                    record.updated_at = time.time()
                    self._persist()
                    return record
            record = QuarantineRecord(
                record_id=f"qr_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                workflow_id=workflow_id,
                owner_id=owner_id,
                node_id=node_id,
                reason=reason,
                failure_class=failure_class,
                signature=signature,
                trigger=trigger,
                attempts=attempts,
                graph_version=graph_version,
            )
            self._records[record.record_id] = record
            self._persist()
            return record

    def mark_replayed(self, record_id: str, *, graph_version: int, note: str = "") -> QuarantineRecord:
        with _PERSIST_LOCK:
            record = self._require(record_id)
            record.status = QuarantineStatus.REPLAYED
            record.replay_graph_version = graph_version
            record.replay_refusal = ""
            record.resolution_note = note or record.resolution_note
            record.updated_at = time.time()
            self._persist()
            return record

    def mark_replay_refused(self, record_id: str, *, reason: str) -> QuarantineRecord:
        """Record a refused replay. The record stays quarantined — a refusal is
        not a resolution, and closing the row would lose the work."""
        with _PERSIST_LOCK:
            record = self._require(record_id)
            record.replay_refusal = reason
            record.updated_at = time.time()
            self._persist()
            return record

    def discard(self, record_id: str, *, note: str) -> QuarantineRecord:
        if not note or not note.strip():
            raise ValueError("discarding a quarantined node requires a reason")
        with _PERSIST_LOCK:
            record = self._require(record_id)
            record.status = QuarantineStatus.DISCARDED
            record.discarded_at = time.time()
            record.resolution_note = note.strip()
            record.updated_at = time.time()
            self._persist()
            return record

    def _require(self, record_id: str) -> QuarantineRecord:
        record = self._records.get(record_id)
        if record is None:
            raise KeyError(f"quarantine record '{record_id}' not found")
        return record

    # ------------------------------------------------------------------- reads

    def get(self, record_id: str) -> QuarantineRecord | None:
        return self._records.get(record_id)

    def list(
        self,
        *,
        owner_id: str | None = None,
        status: QuarantineStatus | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[QuarantineRecord]:
        """Bounded, owner-scoped queue read, oldest first.

        Oldest first because the queue is worked, not admired: the longest-dead
        node is the one most likely to be blocking a human. ``limit`` bounds the
        page; the count of everything matching is available via ``__len__``.
        """
        records = list(self._records.values())
        if owner_id is not None:
            records = [record for record in records if record.owner_id == owner_id]
        if status is not None:
            records = [record for record in records if record.status is status]
        if run_id is not None:
            records = [record for record in records if record.run_id == run_id]
        records.sort(key=lambda record: (record.quarantined_at, record.record_id))
        return records[: max(0, limit)]

    def open_for_run(self, run_id: str) -> list[QuarantineRecord]:
        return [record for record in self._records.values() if record.run_id == run_id and record.status is QuarantineStatus.QUARANTINED]

    def forget_run(self, run_id: str) -> int:
        """Drop every record for a run whose data the operator purged."""
        with _PERSIST_LOCK:
            doomed = [key for key, record in self._records.items() if record.run_id == run_id]
            for key in doomed:
                del self._records[key]
            if doomed:
                self._persist()
            return len(doomed)

    def __len__(self) -> int:
        return len(self._records)


#: One lock for every quarantine store in the process; same rationale and same
#: boundary as the lease store's.
_PERSIST_LOCK = threading.RLock()
