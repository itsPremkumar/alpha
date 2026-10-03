"""Durable worker leases for workflow node attempts.

Why this exists
---------------
A node is dispatched on some worker. While it runs, the engine's own view says
one thing (`node_states[nid] = RUNNING`) and the world says another — the
worker may be alive, wedged, or already dead. Nothing in the run's event log
answers "who owns this attempt, and may their result still be believed?"

That question has to be answered *durably*, because the case that matters is
the one where the process that knew the answer is gone:

* **Recovery.** A run replayed from its event log after a crash folds
  ``node_started`` into ``RUNNING`` and never folds anything back, because the
  completing event was never written. The scheduler admits only
  ``PENDING``/``READY``, so that node is never dispatched again and the run is
  stuck forever. A lease that expired proves its worker is gone, which is what
  makes ``reconcile_orphaned_nodes`` able to say so instead of guessing.
* **Late results.** A fenced deadline discards a result the runtime can see.
  A result arriving from a *different* attempt — a worker that outlived its
  lease — must be recognisable as stale by anyone who receives it, not just by
  the thread that was waiting.

The three tokens that make that possible
-----------------------------------------
``attempt_id``
    Identifies *this* try. A retry is a new attempt, never a continuation.
``fence_token``
    Monotonic per ``(run, node)``, persisted so it keeps increasing across
    restarts. The classic fencing token: a worker holding ``4`` cannot
    influence state claimed under ``5``, no matter when its result lands.
``graph_version``
    The revision the attempt was dispatched against. A run replanned while a
    node was in flight moves the graph forward, so a result computed against
    the old shape is reported ``superseded_revision`` rather than silently
    folded into a plan it does not match.

Honesty rules
-------------
* **The store is single-Gateway, exactly like the event log beside it.**
  It is an atomically-replaced local JSON file: restart-recoverable for one
  Gateway process, *not* a cross-process lock service. Two processes sharing
  one directory can lose an update in the read-modify-write window. Do not
  describe it as a distributed lease or as cross-process exactly-once
  coordination — the same boundary
  [`docs/DYNAMIC_WORKFLOWS.md`](../../../../docs/DYNAMIC_WORKFLOWS.md) draws
  for the event sink.
* **An unverifiable result is never accepted.** ``check_result`` returns
  ``UNKNOWN_LEASE`` when no fence was ever issued for the key — the caller
  cannot prove the attempt was ever ours, so it is not ``ACCEPTED``.
* **Expiry is an observation, not a kill.** ``is_expired`` compares wall-clock
  time against the last heartbeat; reclaiming an expired lease says the worker
  is *presumed* gone. The reclaimed lease is returned so the caller can record
  what it superseded rather than dropping it on the floor.
* **Failure to persist must not look like success.** A write error raises
  ``LeaseStoreError`` rather than leaving a silently in-memory-only manager
  that would report durability it does not have.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_LEASE_TTL_SECONDS",
    "LeaseManager",
    "LeaseStoreError",
    "ResultVerdict",
    "WorkerLease",
]
DEFAULT_LEASE_TTL_SECONDS = 60.0

# One lock for every lease manager in the process: lease operations are
# microsecond-scale, and a single guard is simpler to reason about than a
# per-directory one. It does NOT protect against another process — see the
# module docstring's single-Gateway boundary.
_PERSIST_LOCK = threading.RLock()

_SCHEMA_VERSION = 1


class LeaseStoreError(RuntimeError):
    """The lease file could not be read or written (fail-closed, never silent)."""


class ResultVerdict(StrEnum):
    """Whether a result from an attempt may still be believed."""

    ACCEPTED = "accepted"
    STALE_LEASE = "stale_lease"
    SUPERSEDED_REVISION = "superseded_revision"
    UNKNOWN_LEASE = "unknown_lease"


class WorkerLease(BaseModel):
    """One in-flight claim on a node by a worker."""

    node_id: str
    run_id: str
    worker_id: str
    #: Identifies this try; a retry is a new attempt, never a continuation.
    attempt_id: str = ""
    #: Monotonic per ``(run, node)`` and persisted, so it survives restarts.
    fence_token: int = 0
    #: The graph revision this attempt was dispatched against.
    graph_version: int = 0
    acquired_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    heartbeat_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        hb = datetime.fromisoformat(self.heartbeat_at)
        return datetime.now(UTC) > hb + timedelta(seconds=self.ttl_seconds)

    @property
    def key(self) -> str:
        return f"{self.run_id}:{self.node_id}"

    def remaining_seconds(self) -> float:
        """Seconds left on the lease; ``0.0`` once expired (never negative)."""
        hb = datetime.fromisoformat(self.heartbeat_at)
        deadline = hb + timedelta(seconds=self.ttl_seconds)
        return max(0.0, (deadline - datetime.now(UTC)).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class LeaseManager:
    """Durable, fenced lease registry for workflow node attempts.

    Construct with ``store_dir`` to persist (the engine does); construct
    without it for a purely process-local manager, which is what a throwaway
    dry run wants — ``simulate_run`` must not write into the real store.
    """

    def __init__(self, store_dir: Path | None = None, *, store_name: str = "leases.json") -> None:
        self._path = (Path(store_dir) / store_name) if store_dir is not None else None
        self._leases: dict[str, WorkerLease] = {}
        self._fences: dict[str, int] = {}
        if self._path is not None:
            self._load()

    # ---------------------------------------------------------------- persistence

    @property
    def store_path(self) -> Path | None:
        """Where leases are persisted, or ``None`` when process-local."""
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
            raise LeaseStoreError(f"failed to read lease store {path}: {exc}") from exc
        if not raw.strip():
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LeaseStoreError(f"lease store {path} is not valid JSON: {exc}") from exc
        version = payload.get("schema_version")
        if version != _SCHEMA_VERSION:
            raise LeaseStoreError(f"lease store {path} has unsupported schema_version {version!r} (expected {_SCHEMA_VERSION})")
        for key, entry in (payload.get("leases") or {}).items():
            try:
                self._leases[key] = WorkerLease.model_validate(entry)
            except Exception as exc:  # noqa: BLE001 - a malformed record is a store error, not a crash
                raise LeaseStoreError(f"lease store {path} has a malformed lease for {key!r}: {exc}") from exc
        self._fences = {str(k): int(v) for k, v in (payload.get("fences") or {}).items()}

    def _persist(self) -> None:
        path = self._path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        try:
            # The snapshot is taken INSIDE the lock.  A wave releases its leases
            # from several threads at once, so building the payload before
            # taking it lets a concurrent ``del`` land mid-iteration and raise
            # "dictionary changed size during iteration" out of a node's
            # ``finally`` — an engine-internal race surfacing as a node failure.
            with _PERSIST_LOCK:
                payload = {
                    "schema_version": _SCHEMA_VERSION,
                    "leases": {key: lease.to_dict() for key, lease in self._leases.items()},
                    "fences": dict(self._fences),
                }
                tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise LeaseStoreError(f"failed to persist lease store {path}: {exc}") from exc

    # ---------------------------------------------------------------------- leases

    def get_lease(self, run_id: str, node_id: str) -> WorkerLease | None:
        return self._leases.get(f"{run_id}:{node_id}")

    def acquire_lease(
        self,
        run_id: str,
        node_id: str,
        worker_id: str,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        *,
        attempt_id: str | None = None,
        graph_version: int = 0,
    ) -> WorkerLease | None:
        """Claim a node attempt for ``worker_id``.

        Returns the lease (carrying the fresh ``fence_token``) on success and
        ``None`` when a *different* worker still holds a live lease. An
        expired lease is taken over — that worker is presumed gone — and the
        takeover bumps the fence, which is what fences its late results out.
        """
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        key = f"{run_id}:{node_id}"
        # The whole read-modify-write (examine → bump fence → record) is one
        # unit: releasing it between the fence read and the write would let two
        # attempts mint the same fence token, and a fence that two attempts
        # share is no fence at all.
        with _PERSIST_LOCK:
            existing = self._leases.get(key)
            if existing is not None and not existing.is_expired and existing.worker_id != worker_id:
                return None
            fence = self._fences.get(key, 0) + 1
            self._fences[key] = fence
            lease = WorkerLease(
                node_id=node_id,
                run_id=run_id,
                worker_id=worker_id,
                attempt_id=attempt_id if attempt_id is not None else f"{worker_id}#{fence}",
                fence_token=fence,
                graph_version=graph_version,
                ttl_seconds=ttl_seconds,
            )
            self._leases[key] = lease
            self._persist()
        return lease

    def heartbeat(self, run_id: str, node_id: str, worker_id: str, *, fence_token: int | None = None) -> bool:
        """Extend a lease the caller still owns.

        A stale worker presenting an older ``fence_token`` is refused: renewing
        a claim somebody else has superseded is how a zombie outlives its
        lease.
        """
        key = f"{run_id}:{node_id}"
        with _PERSIST_LOCK:
            lease = self._leases.get(key)
            if lease is None or lease.worker_id != worker_id:
                return False
            if fence_token is not None and lease.fence_token != fence_token:
                return False
            lease.heartbeat_at = datetime.now(UTC).isoformat()
            self._persist()
        return True

    def release_lease(self, run_id: str, node_id: str, worker_id: str | None = None, *, fence_token: int | None = None) -> None:
        """Drop a lease the caller still owns.

        ``worker_id``/``fence_token`` are optional for compatibility with the
        pre-fence callers; supplying the fence is what makes a late release
        from a superseded attempt a no-op instead of a theft.
        """
        key = f"{run_id}:{node_id}"
        with _PERSIST_LOCK:
            lease = self._leases.get(key)
            if lease is None:
                return
            if worker_id is not None and lease.worker_id != worker_id:
                return
            if fence_token is not None and lease.fence_token != fence_token:
                return
            del self._leases[key]
            # The fence deliberately survives release: it is the monotonic counter
            # that keeps a late result from being indistinguishable from a fresh
            # one after the record is gone.
            self._persist()

    def get_stale_leases(self) -> list[WorkerLease]:
        """Expired leases, still recorded — an observation, not a mutation."""
        with _PERSIST_LOCK:
            return [lease for lease in self._leases.values() if lease.is_expired]

    def reclaim_expired(self) -> list[WorkerLease]:
        """Remove every expired lease and return what was superseded.

        Each reclaimed key has its **fence advanced**: an expired lease means
        its worker is presumed gone, so its token must stop being current —
        otherwise a late result carrying the old fence would still verify as
        :attr:`ResultVerdict.ACCEPTED` and write through after we already
        decided the attempt was over.
        """
        with _PERSIST_LOCK:
            expired = [lease for lease in self._leases.values() if lease.is_expired]
            if not expired:
                return []
            for lease in expired:
                self._leases.pop(lease.key, None)
                self._fences[lease.key] = self._fences.get(lease.key, 0) + 1
            self._persist()
        return expired

    def reclaim(self, run_id: str, node_id: str) -> WorkerLease | None:
        """Force-release one lease and fence its worker's late results out.

        Used when an attempt is judged orphaned without waiting for its TTL —
        the caller has independently established that nobody is running it.
        """
        key = f"{run_id}:{node_id}"
        with _PERSIST_LOCK:
            lease = self._leases.pop(key, None)
            if lease is None:
                return None
            self._fences[key] = self._fences.get(key, 0) + 1
            self._persist()
        return lease

    def forget_run(self, run_id: str) -> int:
        """Drop every lease and fence belonging to a run (e.g. cancelled).

        Dropping the fences is safe: a late result then reports
        ``unknown_lease``, which is refused for the same reason a stale one
        is — an unverifiable attempt is never accepted.
        """
        with _PERSIST_LOCK:
            leases_doomed = [key for key in self._leases if key.startswith(f"{run_id}:")]
            fences_doomed = [key for key in self._fences if key.startswith(f"{run_id}:")]
            for key in leases_doomed:
                del self._leases[key]
            for key in fences_doomed:
                del self._fences[key]
            if leases_doomed or fences_doomed:
                self._persist()
        return len(leases_doomed)

    # ------------------------------------------------------------------ fencing

    def check_result(
        self,
        run_id: str,
        node_id: str,
        *,
        worker_id: str,
        fence_token: int,
        graph_version: int | None = None,
    ) -> ResultVerdict:
        """Decide whether a result from an attempt may still be believed.

        Checked *before* the result is folded into run state, so a worker that
        outlived its lease or ran against an older plan revision cannot write
        through. An unverifiable key is ``unknown_lease``, never
        ``accepted`` — silence is not proof.
        """
        key = f"{run_id}:{node_id}"
        # One atomic observation: reading the fence and the lease under the
        # same lock keeps a concurrent release from landing between them.
        with _PERSIST_LOCK:
            current_fence = self._fences.get(key)
            if current_fence is None:
                return ResultVerdict.UNKNOWN_LEASE
            if fence_token != current_fence:
                return ResultVerdict.STALE_LEASE
            if graph_version is not None:
                lease = self._leases.get(key)
                recorded = lease.graph_version if lease is not None else None
                # After release the graph version is no longer recorded; a caller
                # that supplies one against an unknown record is unverifiable on
                # that axis but still holds the current fence, so the fence alone
                # does not reject it.
                if recorded is not None and recorded != graph_version:
                    return ResultVerdict.SUPERSEDED_REVISION
        return ResultVerdict.ACCEPTED
