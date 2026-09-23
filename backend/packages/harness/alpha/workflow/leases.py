"""Worker lease tracking and stale execution recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field


class WorkerLease(BaseModel):
    node_id: str
    run_id: str
    worker_id: str
    acquired_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    heartbeat_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    ttl_seconds: float = 60.0

    @property
    def is_expired(self) -> bool:
        hb = datetime.fromisoformat(self.heartbeat_at)
        return datetime.now(UTC) > hb + timedelta(seconds=self.ttl_seconds)


class LeaseManager:
    """In-memory lease manager for coordinating dynamic workflow node execution."""

    def __init__(self) -> None:
        self._leases: dict[str, WorkerLease] = {}

    def acquire_lease(self, run_id: str, node_id: str, worker_id: str, ttl_seconds: float = 60.0) -> bool:
        key = f"{run_id}:{node_id}"
        existing = self._leases.get(key)
        if existing and not existing.is_expired and existing.worker_id != worker_id:
            return False

        self._leases[key] = WorkerLease(
            node_id=node_id,
            run_id=run_id,
            worker_id=worker_id,
            ttl_seconds=ttl_seconds,
        )
        return True

    def heartbeat(self, run_id: str, node_id: str, worker_id: str) -> bool:
        key = f"{run_id}:{node_id}"
        lease = self._leases.get(key)
        if not lease or lease.worker_id != worker_id:
            return False
        lease.heartbeat_at = datetime.now(UTC).isoformat()
        return True

    def release_lease(self, run_id: str, node_id: str, worker_id: str) -> None:
        key = f"{run_id}:{node_id}"
        lease = self._leases.get(key)
        if lease and lease.worker_id == worker_id:
            del self._leases[key]

    def get_stale_leases(self) -> list[WorkerLease]:
        return [lease for lease in self._leases.values() if lease.is_expired]
