"""Durable, append-only event log + run projections for the DWE (W-N1).

The engine's in-memory ``WorkflowEventDispatcher`` keeps a process-local list
(``alpha.workflow.events``), so a restart loses every event and a fresh
process cannot see a run that is still executing elsewhere. This module is the
durable layer the whole dynamic-workflow system needs: every emitted event is
appended to ``{store}/events/{run_id}.jsonl`` and every run can be projected
to ``{store}/runs/{run_id}.json`` and hydrated back into a fresh engine.

Contracts live in :mod:`alpha.workflow.schemas` (versioned records); this
module is the storage + honesty policy around them.

Honesty policy (deliberate, fail-closed):

* An append that cannot be persisted RAISES :class:`DurableEventLogError`. The
  caller decides what to do; the log never swallows a write failure.
* A corrupt log TAIL is never silently skipped. ``read_events`` stops at the
  first unparseable line and returns a disclosure naming the line number and
  the parse error; callers surface it (hydration report / REST response).
* A stale projection is never installed as if current: if the durable log
  contains events beyond the projection's ``last_seq``, hydration refuses the
  projection and says so (``stale_projections``) instead of pretending the
  run is at an older point.
* Idempotency keys (``{run}:{graph_version}:{node}:{attempt}:{input_hash}``,
  DOC-A section 13.3) make node-attempt appends idempotent per run: replaying
  the same key returns the already-recorded line instead of duplicating it.
  A key is never invented when the caller does not supply one.
* Sync library. File IO is blocking: event-loop callers offload (``asyncio.
  to_thread`` / ``alpha.utils.file_io.run_file_io``). Locks are per-instance.

A gateway process wires this in by attaching it to the global event
dispatcher (see ``app.gateway.routers.workflows``); unit tests construct a
store against a temp dir, so no test ever writes to a real workspace.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json
from alpha.workflow.events import WorkflowEvent, redact_event_payload
from alpha.workflow.models import WorkflowDefinition, WorkflowGraph, WorkflowRun
from alpha.workflow.schemas import (
    HydrationReport,
    PersistedEventRecord,
    PersistedRunSnapshot,
)

logger = logging.getLogger(__name__)

# Run ids become path segments.  Keep the accepted alphabet deliberately
# narrower than arbitrary URL text so traversal and Windows reserved-name
# tricks cannot reach outside the configured store.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# Multiple Gateway workers can construct separate DurableEventLog instances
# for the same root.  Serialize sequence allocation/append per root, not per
# Python object.
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


def _root_lock(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise DurableEventLogError(f"invalid workflow run id {run_id!r}: expected 1-128 chars of [A-Za-z0-9._-] starting alphanumeric")
    if run_id.split(".", 1)[0].lower() in _RESERVED_WINDOWS_NAMES:
        raise DurableEventLogError(f"invalid workflow run id {run_id!r}: reserved Windows device name")
    return run_id


EVENT_LOG_SCHEMA_VERSION = PersistedEventRecord.model_fields["schema_version"].default


class DurableEventLogError(RuntimeError):
    """The durable event log could not satisfy a request (fail-closed)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DurableEventLog:
    """Append-only JSONL event log + atomic run projections."""

    def __init__(self, store_dir: Path | None = None) -> None:
        self.root = Path(store_dir) if store_dir is not None else runtime_home() / "workflow_store"
        self.events_dir = self.root / "events"
        self.runs_dir = self.root / "runs"
        self._lock = _root_lock(self.root)
        # per-run writer state, rebuilt lazily from disk (restart-safe)
        self._seq: dict[str, int] = {}
        self._idempotency: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def event_log_path(self, run_id: str) -> Path:
        return self.events_dir / f"{_validate_run_id(run_id)}.jsonl"

    def run_projection_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{_validate_run_id(run_id)}.json"

    def probe_writable(self) -> tuple[bool, str]:
        """Can this store accept an append right now? (start-up gate)

        Returns ``(ok, detail)``; ``detail`` is the real reason on failure.
        The workflow router refuses to start a run on an unwritable store:
        a run that cannot be journaled must not start.
        """
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            probe = self.events_dir / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return True, "writable"

    # ------------------------------------------------------------------
    # Append path
    # ------------------------------------------------------------------
    def _assert_tail_intact(self, run_id: str) -> None:
        """O(1) guard: the log's last line must parse.

        A corrupt tail means every sequence number after it is untrustworthy,
        so an append must refuse even when this instance already cached the
        run's seq (e.g. the damage arrived from another writer or from a
        partial write). Only the tail of the file is read, never the whole log.
        """
        path = self.event_log_path(run_id)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size == 0:
            return
        with path.open("rb") as handle:
            handle.seek(max(0, size - 65536))
            chunk = handle.read().decode("utf-8", errors="replace")
        for line in reversed(chunk.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                PersistedEventRecord.model_validate_json(stripped)
            except ValueError as exc:
                msg = f"event log for run {run_id!r} has a corrupt tail; append refused until it is repaired: {type(exc).__name__}: {exc}"
                logger.error(msg)
                raise DurableEventLogError(msg) from exc
            return

    def _load_run_state(self, run_id: str) -> None:
        """Rebuild seq + idempotency keys for a run from its on-disk log.

        The tail is verified on EVERY call (see ``_assert_tail_intact``); the
        full scan happens only the first time this instance touches the run.
        """
        self._assert_tail_intact(run_id)
        if run_id in self._seq:
            return
        last = 0
        keys: set[str] = set()
        path = self.event_log_path(run_id)
        if path.exists():
            _, disclosures = self.read_events(run_id)
            if disclosures:
                # A corrupt tail means the tail's seq cannot be trusted for
                # appending: refuse rather than numbering past the damage.
                raise DurableEventLogError(f"event log for run {run_id!r} has a corrupt tail; append refused until it is repaired: {disclosures[0]}")
            for record in self._read_records(run_id)[0]:
                last = max(last, record.seq)
                if record.idempotency_key:
                    keys.add(record.idempotency_key)
        self._seq[run_id] = last
        self._idempotency[run_id] = keys

    def _read_records(self, run_id: str) -> tuple[list[PersistedEventRecord], list[dict[str, Any]]]:
        path = self.event_log_path(run_id)
        if not path.exists():
            return [], []
        records: list[PersistedEventRecord] = []
        disclosures: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    records.append(PersistedEventRecord.model_validate_json(stripped))
                except ValueError as exc:
                    # Stop at the FIRST bad line: everything after a corrupt
                    # line is untrustworthy ordering, so it is disclosed, not
                    # skipped, and never handed to a replay.
                    disclosures.append({"line_number": line_number, "error": f"{type(exc).__name__}: {exc}", "raw_prefix": stripped[:120]})
                    break
        return records, disclosures

    def append(self, event: WorkflowEvent, *, idempotency_key: str | None = None) -> PersistedEventRecord:
        """Append one event durably. Raises on any persistence failure."""
        run_id = event.workflow_run_id
        effective_key = idempotency_key if idempotency_key is not None else event.idempotency_key
        with self._lock:
            self._load_run_state(run_id)
            if effective_key is not None and effective_key in self._idempotency[run_id]:
                # Idempotent replay: the identical key is already durable.
                for record in self._read_records(run_id)[0]:
                    if record.idempotency_key == effective_key:
                        return record
            seq = self._seq[run_id] + 1
            record = PersistedEventRecord(
                seq=seq,
                event_id=event.event_id,
                workflow_run_id=run_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                payload=redact_event_payload(json.loads(json.dumps(event.payload, default=str))),
                idempotency_key=effective_key,
                recorded_at=_now(),
            )
            try:
                self.events_dir.mkdir(parents=True, exist_ok=True)
                with self.event_log_path(run_id).open("a", encoding="utf-8") as handle:
                    handle.write(record.model_dump_json() + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise DurableEventLogError(f"failed to append event for run {run_id!r}: {exc}") from exc
            self._seq[run_id] = seq
            if effective_key is not None:
                self._idempotency[run_id].add(effective_key)
            return record

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------
    def read_events(self, run_id: str) -> tuple[list[WorkflowEvent], list[dict[str, Any]]]:
        """Events for a run + any corrupt-tail disclosures (never hidden)."""
        records, disclosures = self._read_records(run_id)
        events = [
            WorkflowEvent(
                event_id=record.event_id,
                workflow_run_id=record.workflow_run_id,
                event_type=record.event_type,
                timestamp=record.timestamp,
                idempotency_key=record.idempotency_key,
                payload=record.payload,
            )
            for record in records
        ]
        return events, disclosures

    def records_for(self, run_id: str) -> tuple[list[PersistedEventRecord], list[dict[str, Any]]]:
        """Validated persisted records for a run + corrupt-tail disclosures."""
        return self._read_records(run_id)

    def last_seq(self, run_id: str) -> int:
        records, disclosures = self._read_records(run_id)
        if disclosures:
            raise DurableEventLogError(f"event log for run {run_id!r} has a corrupt tail; last_seq is untrustworthy")
        return max((record.seq for record in records), default=0)

    def list_runs(self) -> list[str]:
        run_ids: set[str] = set()
        if self.runs_dir.exists():
            run_ids.update(path.stem for path in self.runs_dir.glob("*.json"))
        if self.events_dir.exists():
            run_ids.update(path.stem for path in self.events_dir.glob("*.jsonl"))
        return sorted(run_ids)

    # ------------------------------------------------------------------
    # Projection path
    # ------------------------------------------------------------------
    def project(
        self,
        *,
        run: WorkflowRun,
        definition: WorkflowDefinition | None,
        graphs: dict[str, WorkflowGraph],
        last_event: PersistedEventRecord | None,
        event_count: int,
    ) -> PersistedRunSnapshot:
        """Materialize the run's projection atomically and return it."""
        snapshot = PersistedRunSnapshot(
            run=run,
            definition=definition,
            graphs=graphs,
            last_seq=last_event.seq if last_event is not None else 0,
            event_count=event_count,
            source_event=({"event_id": last_event.event_id, "event_type": last_event.event_type, "timestamp": last_event.timestamp} if last_event is not None else {}),
        )
        try:
            atomic_write_json(self.run_projection_path(run.run_id), snapshot.model_dump(mode="json"))
        except OSError as exc:
            raise DurableEventLogError(f"failed to persist projection for run {run.run_id!r}: {exc}") from exc
        return snapshot

    def load_snapshot(self, run_id: str) -> PersistedRunSnapshot | None:
        path = self.run_projection_path(run_id)
        if not path.exists():
            return None
        return PersistedRunSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------
    def hydrate(self, engine: Any, *, owner_id: str | None = None) -> HydrationReport:
        """Install persisted runs into a fresh engine; report every refusal.

        When ``owner_id`` is supplied, runs owned by another principal are
        treated as invisible rather than hydrated into the caller's engine.
        """
        hydrated: list[str] = []
        skipped: list[str] = []
        corrupt: list[dict[str, str]] = []
        stale: list[dict[str, str]] = []
        missing_graphs: list[str] = []
        missing_definitions: list[str] = []
        disclosures: list[str] = []

        for run_id in self.list_runs():
            if run_id in getattr(engine, "runs", {}):
                skipped.append(run_id)
                continue
            try:
                snapshot = self.load_snapshot(run_id)
            except ValueError as exc:
                corrupt.append({"run_id": run_id, "error": f"projection failed validation: {type(exc).__name__}: {exc}"})
                continue
            if snapshot is None:  # pragma: no cover - list_runs saw the file
                corrupt.append({"run_id": run_id, "error": "projection disappeared between listing and read"})
                continue
            if owner_id and snapshot.run.owner_id not in (None, owner_id):
                skipped.append(run_id)
                disclosures.append(f"run {run_id}: owner scope excludes this resource")
                continue
            # Durable events beyond the projection mean it is behind: refuse
            # to install it as if current.
            try:
                durable_last = self.last_seq(run_id)
            except DurableEventLogError as exc:
                corrupt.append({"run_id": run_id, "error": f"event log unusable: {exc}"})
                continue
            if durable_last > snapshot.last_seq:
                stale.append(
                    {
                        "run_id": run_id,
                        "detail": f"projection at seq {snapshot.last_seq} but durable log has seq {durable_last}",
                    }
                )
                continue
            engine.runs[snapshot.run.run_id] = snapshot.run
            hydrated.append(run_id)
            if snapshot.definition is not None:
                definition = snapshot.definition
                # The serialized definition may carry the latest compatibility
                # projection, while replay needs the authored graph revision.
                # Recover the lowest durable graph revision when available and
                # keep it as process-local replay metadata.
                graph_versions: list[tuple[int, WorkflowGraph]] = []
                for key, graph in snapshot.graphs.items():
                    prefix = f"{definition.id}:v"
                    if not key.startswith(prefix):
                        continue
                    try:
                        graph_versions.append((int(key[len(prefix) :]), graph))
                    except ValueError:
                        continue
                if graph_versions:
                    definition._base_graph = deepcopy(min(graph_versions, key=lambda item: item[0])[1])
                engine.definitions[definition.id] = definition
            else:
                missing_definitions.append(run_id)
            for key, graph in snapshot.graphs.items():
                engine.graphs.setdefault(key, graph)
            graph_key = f"{snapshot.run.workflow_id}:v{snapshot.run.graph_version}"
            if graph_key not in snapshot.graphs:
                missing_graphs.append(run_id)
                disclosures.append(f"run {run_id}: no graph {graph_key} in the projection; continuation may refuse honestly")

        if not self.list_runs():
            status = "empty"
        elif corrupt or stale or missing_graphs or missing_definitions or skipped:
            status = "degraded"
        else:
            status = "ok"
        return HydrationReport(
            status=status,  # type: ignore[arg-type]
            store_dir=str(self.root),
            hydrated_runs=hydrated,
            skipped_existing=skipped,
            corrupt_runs=corrupt,
            stale_projections=stale,
            missing_graphs=missing_graphs,
            missing_definitions=missing_definitions,
            disclosures=disclosures,
        )


def node_attempt_idempotency_key(
    *,
    run_id: str,
    graph_version: int,
    node_id: str,
    attempt: int,
    input_hash: str,
) -> str:
    """DOC-A section 13.3 key for a node attempt: the caller supplies real
    inputs only; this helper never invents a hash or an attempt number."""
    return f"{run_id}:{graph_version}:{node_id}:{attempt}:{input_hash}"


def iter_persisted_records(store: DurableEventLog, run_ids: Iterable[str]) -> Iterable[PersistedEventRecord]:
    """Convenience: validated records across runs (test/diagnostic helper)."""
    for run_id in run_ids:
        yield from store._read_records(run_id)[0]
