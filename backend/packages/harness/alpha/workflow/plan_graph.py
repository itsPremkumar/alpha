"""Plan-graph revision store with optimistic concurrency (W-N1).

Every graph revision of a workflow is persisted as a :class:`PlanVersion`
under ``{store}/plans/{workflow_id}/v{version}.json``. Replaying or patching a
run therefore has a durable record of the graph it ran on, and two writers
racing on the same version are rejected deterministically (the kernel's
optimistic-concurrency rule) instead of silently losing an update.

Contracts share :data:`alpha.workflow.schemas.SCHEMA_VERSION`; records are
additive-only (extra keys on read are ignored by the pydantic model).

Honesty policy:

* A corrupt revision file RAISES :class:`PlanGraphError` — a damaged history
  is never reported as "no revisions" (which would look like a fresh workflow).
* ``compare_and_set`` raises :class:`PlanVersionConflict` on a stale expected
  version; it never overwrites another writer's revision.
* Writes are atomic (tmp + ``os.replace``, UTF-8).

Sync library: event-loop callers must offload to a thread executor.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alpha.config.runtime_paths import runtime_home
from alpha.workflow.models import WorkflowGraph
from alpha.workflow.schemas import SCHEMA_VERSION

_WORKFLOW_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
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


class PlanGraphError(RuntimeError):
    """The plan-graph store could not satisfy a request (fail-closed)."""


class PlanVersionConflict(PlanGraphError):
    """Optimistic-concurrency rejection: someone else advanced the version."""


class PlanVersion(BaseModel):
    """One durable graph revision of a workflow."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(default=SCHEMA_VERSION)
    workflow_id: str
    owner_id: str | None = None
    version: int = Field(..., ge=0)
    graph: WorkflowGraph
    source: Literal["register", "patch", "replay", "hydration", "manual"] = "register"
    note: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PlanGraphStore:
    """Durable graph-revision history with compare-and-set semantics."""

    def __init__(self, store_dir: Path | None = None) -> None:
        self.root = Path(store_dir) if store_dir is not None else runtime_home() / "workflow_store" / "plans"
        self._lock = _root_lock(self.root)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def _workflow_dir(self, workflow_id: str) -> Path:
        if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise PlanGraphError(f"invalid workflow_id {workflow_id!r}: expected 1-128 chars of [A-Za-z0-9._-] starting alphanumeric (path-safe)")
        return self.root / workflow_id

    def revision_path(self, workflow_id: str, version: int) -> Path:
        return self._workflow_dir(workflow_id) / f"v{version}.json"

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, workflow_id: str, version: int) -> PlanVersion | None:
        path = self.revision_path(workflow_id, version)
        if not path.exists():
            return None
        try:
            return PlanVersion.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PlanGraphError(f"corrupt plan revision {workflow_id!r} v{version}: {type(exc).__name__}: {exc}") from exc

    def list_versions(self, workflow_id: str) -> list[int]:
        directory = self._workflow_dir(workflow_id)
        if not directory.exists():
            return []
        versions: list[int] = []
        for path in directory.glob("v*.json"):
            try:
                versions.append(int(path.stem[1:]))
            except ValueError:
                raise PlanGraphError(f"unexpected revision filename {path.name!r} in {directory}")
        return sorted(versions)

    def history(self, workflow_id: str, *, owner_id: str | None = None) -> list[PlanVersion]:
        records = [record for version in self.list_versions(workflow_id) if (record := self.get(workflow_id, version)) is not None]
        if owner_id is not None:
            records = [record for record in records if record.owner_id == owner_id]
        return records

    def latest(self, workflow_id: str) -> PlanVersion | None:
        versions = self.list_versions(workflow_id)
        return self.get(workflow_id, versions[-1]) if versions else None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def save(self, record: PlanVersion) -> PlanVersion:
        path = self.revision_path(record.workflow_id, record.version)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(record.to_dict(), handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)
        except OSError as exc:
            raise PlanGraphError(f"failed to persist plan revision {record.workflow_id!r} v{record.version}: {exc}") from exc
        return record

    def record_revision(
        self,
        workflow_id: str,
        graph: WorkflowGraph,
        *,
        source: Literal["register", "patch", "replay", "hydration", "manual"] = "register",
        note: str = "",
        version: int | None = None,
        owner_id: str | None = None,
    ) -> PlanVersion:
        """Append a revision. Version defaults to ``graph.version``."""
        target = graph.version if version is None else version
        if target < 0:
            raise PlanGraphError(f"version must be >= 0, got {target}")
        with self._lock:
            existing = self.get(workflow_id, target)
            if existing is not None:
                raise PlanVersionConflict(f"plan revision {workflow_id!r} v{target} already exists (recorded {existing.created_at}); use compare_and_set to advance deliberately")
            return self.save(PlanVersion(workflow_id=workflow_id, owner_id=owner_id, version=target, graph=graph, source=source, note=note))

    def compare_and_set(
        self,
        workflow_id: str,
        *,
        expected_version: int,
        new_graph: WorkflowGraph,
        source: Literal["patch", "replay", "manual"] = "patch",
        note: str = "",
        owner_id: str | None = None,
    ) -> PlanVersion:
        """Advance the workflow to ``new_graph.version`` only if the current
        latest revision is exactly ``expected_version``."""
        with self._lock:
            latest = self.latest(workflow_id)
            if owner_id is not None and latest is not None and latest.owner_id != owner_id:
                raise PlanVersionConflict(f"plan workflow '{workflow_id!r}' belongs to another owner")
            current = latest.version if latest is not None else -1
            if current != expected_version:
                raise PlanVersionConflict(f"optimistic-concurrency rejection for {workflow_id!r}: expected latest v{expected_version}, found v{current}")
            if new_graph.version <= current:
                raise PlanGraphError(f"new graph version {new_graph.version} must be greater than current v{current} ({workflow_id!r})")
            return self.save(PlanVersion(workflow_id=workflow_id, owner_id=owner_id, version=new_graph.version, graph=new_graph, source=source, note=note))
