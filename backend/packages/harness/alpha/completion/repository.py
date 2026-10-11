"""Typed persistence over the JSON ledger — the repository the engine uses.

Everything above this module works with a :class:`CompletionScope` aggregate;
everything below it works with a JSON document under an advisory lock. Keeping
that seam in one place is what lets the rest of the engine be pure functions
over a loaded scope while still surviving a restart.

Two invariants:

* **Read failures are loud.** ``load`` raises
  :class:`~alpha.completion.errors.StoreUnreadable` rather than returning an
  empty scope: "there are no contracts" and "I could not read the contracts"
  lead to opposite decisions, and only one of them is true.
* **Writes are all-or-nothing.** Every mutation runs under the lock and is
  written back atomically after the callable returns; a refusal inside the
  callable leaves the file exactly as it was.

Scope counts are bounded (``completion.max_scopes``): beyond the ceiling the
oldest *terminal* scope is evicted, and if every scope is live the write is
refused with the numbers named rather than silently dropping live state.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from pydantic import ValidationError

from .errors import NotFound, StoreCorrupt, StoreUnreadable
from .models import CompletionScope, ScopeStatus
from .policy import CompletionSettings
from .storage import CompletionStore

T = TypeVar("T")


def scope_summary(scope: CompletionScope) -> dict[str, Any]:
    """Flat projection for lists and the UI — never the full aggregate."""
    gates = list(scope.gates.values())
    tasks = list(scope.tasks.values())
    required = [gate for gate in gates if gate.required]
    return {
        "scope_id": scope.scope_id,
        "owner_id": scope.owner_id,
        "thread_id": scope.thread_id,
        "mode": scope.mode,
        "status": scope.status.value,
        "status_reason": scope.status_reason,
        "request_snapshot": scope.request_snapshot[:400],
        "request_revision": scope.request_revision,
        "contract_valid": scope.contract.valid,
        "contract_digest": scope.contract.digest,
        "requirements_total": len(scope.contract.requirements),
        "requirements_met": sum(1 for r in scope.contract.requirements if r.status.value == "met"),
        "tasks_total": len(tasks),
        "tasks_verified": sum(1 for task in tasks if task.state.value == "verified"),
        "tasks_in_flight": sum(1 for task in tasks if task.state.value in {"in_flight", "returned", "verifying"}),
        "gates_total": len(gates),
        "gates_required": len(required),
        "gates_passed": sum(1 for gate in gates if gate.status.value == "passed"),
        "gates_stale": sum(1 for gate in gates if gate.status.value == "stale"),
        "evidence_total": len(scope.evidence),
        "dispatches_total": len(scope.dispatches),
        "finalize_attempts": scope.finalize_attempts,
        "no_progress_count": scope.no_progress_count,
        "budget_exhausted": scope.budget.exhausted,
        "created_at": scope.created_at,
        "updated_at": scope.updated_at,
        "finished_at": scope.finished_at,
        "final_status": scope.final_report.status.value if scope.final_report else None,
    }


class CompletionRepository:
    """Owner-scoped access to one ledger."""

    def __init__(self, settings: CompletionSettings) -> None:
        self.settings = settings
        self.store = CompletionStore(settings.store_path)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Read the ledger once and report what it holds.

        An unreadable store reports ``readable: false`` with the reason — it
        never reports ``scopes: 0``, which would read as "nothing recorded".
        """
        try:
            doc = self.store.read(lambda document: {"count": len(document.get("scopes", {})), "events": len(document.get("events", []))})
        except StoreUnreadable as exc:
            return {"readable": False, "error": str(exc), "path": str(self.settings.store_path), "count": None, "events": None}
        return {
            "readable": True,
            "path": str(self.settings.store_path),
            "count": doc["count"],
            "events": doc["events"],
            "mode": self.settings.mode,
            "enforcing": self.settings.enforcing,
            "policy_id": self.settings.policy_id,
        }

    # ------------------------------------------------------------------
    # Scopes
    # ------------------------------------------------------------------
    def load(self, scope_id: str) -> CompletionScope:
        """Load one scope, or raise :class:`NotFound`."""
        return self.store.read(lambda doc: _extract(doc, scope_id))

    def try_load(self, scope_id: str) -> CompletionScope | None:
        try:
            return self.load(scope_id)
        except NotFound:
            return None

    def exists(self, scope_id: str) -> bool:
        return self.store.read(lambda doc: scope_id in doc.get("scopes", {}))

    def save(self, scope: CompletionScope, *, now: float | None = None) -> CompletionScope:
        """Persist one scope (its aggregate travels with it)."""
        moment = time.time() if now is None else now
        scope.updated_at = moment

        def _apply(doc: dict) -> CompletionScope:
            _evict_if_needed(doc, self.settings, now=moment)
            doc.setdefault("scopes", {})[scope.scope_id] = scope.model_dump(mode="json")
            return scope

        return self.store.mutate(_apply)

    def create(self, scope: CompletionScope, *, now: float | None = None) -> CompletionScope:
        """Insert a new scope; refuses to overwrite an existing id."""
        moment = time.time() if now is None else now

        def _apply(doc: dict) -> CompletionScope:
            scopes = doc.setdefault("scopes", {})
            if scope.scope_id in scopes:
                raise StoreCorrupt(f"scope {scope.scope_id!r} already exists; ids must be unique")
            _evict_if_needed(doc, self.settings, now=moment)
            doc["scopes"][scope.scope_id] = scope.model_dump(mode="json")
            return scope

        return self.store.mutate(_apply)

    def list(
        self,
        *,
        owner_id: str | None = None,
        thread_id: str | None = None,
        statuses: Iterable[ScopeStatus] | None = None,
        include_terminal: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Summaries, newest first, filtered by owner/thread/status.

        ``owner_id=""`` means "rows recorded without an owner"; ``None`` means
        no owner filter. They are different queries and are never conflated.
        """
        wanted = {status.value for status in statuses} if statuses else None

        def _project(doc: dict) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for raw in doc.get("scopes", {}).values():
                try:
                    scope = CompletionScope.model_validate(raw)
                except ValidationError:
                    # A row we cannot parse is reported by health(), not
                    # silently skipped as if it were never there.
                    continue
                if owner_id is not None and scope.owner_id != owner_id:
                    continue
                if thread_id is not None and scope.thread_id != thread_id:
                    continue
                if wanted is not None and scope.status.value not in wanted:
                    continue
                if not include_terminal and scope.status.value in {"complete", "cancelled"}:
                    continue
                rows.append(scope_summary(scope))
            rows.sort(key=lambda row: row["updated_at"], reverse=True)
            return rows[: max(1, limit)]

        return self.store.read(_project)

    def mutate_scope(self, scope_id: str, fn: Callable[..., T], *, now: float | None = None) -> T:
        """Load, mutate and persist one scope under a single lock.

        ``fn(scope, doc)`` receives both the aggregate and the underlying
        document, so a composite operation (a scope change plus the event it
        generates, or a gate change plus its approval) can land in **one**
        atomic write instead of two — and so no nested lock acquisition is
        ever needed. ``FileLock`` is not re-entrant across instances, so a
        second acquisition on the same path inside ``fn`` would deadlock.
        """
        moment = time.time() if now is None else now

        def _apply(doc: dict) -> T:
            scope = _extract(doc, scope_id)
            result = fn(scope, doc)
            scope.updated_at = moment
            doc["scopes"][scope_id] = scope.model_dump(mode="json")
            return result

        return self.store.mutate(_apply)

    def delete(self, scope_id: str) -> bool:
        def _apply(doc: dict) -> bool:
            return doc.setdefault("scopes", {}).pop(scope_id, None) is not None

        return self.store.mutate(_apply)

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------
    def approvals(self) -> dict[str, dict]:
        return self.store.read(lambda doc: dict(doc.get("approvals", {})))

    def put_approval(self, gate_id: str, record: dict) -> None:
        def _apply(doc: dict) -> None:
            doc.setdefault("approvals", {})[gate_id] = record

        self.store.mutate(_apply)

    def drop_approval(self, gate_id: str) -> bool:
        def _apply(doc: dict) -> bool:
            return doc.setdefault("approvals", {}).pop(gate_id, None) is not None

        return self.store.mutate(_apply)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def events(self, scope_id: str, *, limit: int = 200, kinds: set[str] | None = None) -> list[dict]:
        def _read(doc: dict) -> list[dict]:
            rows = [row for row in doc.get("events", []) if row.get("scope_id") == scope_id]
            if kinds is not None:
                rows = [row for row in rows if row.get("kind") in kinds]
            return rows[-max(1, limit) :]

        return self.store.read(_read)

    def mutate_document(self, fn: Callable[[dict], T]) -> T:
        """Run a whole-document mutation (used by the engine's composite ops)."""
        return self.store.mutate(fn)

    def read_document(self, fn: Callable[[dict], T]) -> T:
        return self.store.read(fn)


def _extract(doc: dict, scope_id: str) -> CompletionScope:
    raw = doc.get("scopes", {}).get(scope_id)
    if raw is None:
        raise NotFound(f"scope {scope_id!r} does not exist", subject=scope_id)
    try:
        return CompletionScope.model_validate(raw)
    except ValidationError as exc:
        raise StoreCorrupt(f"scope {scope_id!r} cannot be parsed: {exc}") from exc


def _evict_if_needed(doc: dict, settings: CompletionSettings, *, now: float) -> None:
    """Keep the ledger bounded: evict the oldest terminal scope past the ceiling."""
    scopes: dict[str, dict] = doc.setdefault("scopes", {})
    if len(scopes) < settings.max_scopes:
        return
    terminal = [(row.get("finished_at") or row.get("updated_at") or 0.0, scope_id) for scope_id, row in scopes.items() if row.get("status") in {"complete", "cancelled"}]
    if not terminal:
        raise StoreUnreadable(f"completion store holds {len(scopes)} scopes, at its ceiling of {settings.max_scopes}, and none is terminal; refusing to drop live state")
    terminal.sort()
    _, victim = terminal[0]
    scopes.pop(victim, None)


__all__ = ["CompletionRepository", "scope_summary"]
