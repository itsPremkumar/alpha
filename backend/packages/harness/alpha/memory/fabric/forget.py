"""Explicit archive, restore, and hard-forget operations for memory fabric.

These functions are the user/agent-facing deletion seam required by plan
sections 21 and 31.  Hard deletion is audited by the store, pinned records are
refused unless ``force=True``, and every outcome includes counts and reasons.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .lifecycle import TransitionResult
from .models import LifecycleStatus, MemoryScope
from .store import MemoryEnvelopeStore, StoreResult


@dataclass(slots=True)
class ForgetReport:
    """Disclosed report for one explicit lifecycle or deletion operation."""

    status: str
    operation: str
    requested: int = 0
    changed: int = 0
    deleted: int = 0
    pinned_skipped: int = 0
    scope_denied: int = 0
    record_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    forced: bool = False
    reason: str = ""
    error: str = ""
    provenance_status: str = ""
    provenance_error: str = ""
    transitions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the explicit operation completed without partial failure."""

        return self.status in {"succeeded", "empty"}

    def to_dict(self) -> dict[str, Any]:
        """Return an operator/agent-facing honest operation report."""

        return {
            "status": self.status,
            "operation": self.operation,
            "requested": self.requested,
            "changed": self.changed,
            "deleted": self.deleted,
            "pinned_skipped": self.pinned_skipped,
            "scope_denied": self.scope_denied,
            "record_ids": list(self.record_ids),
            "missing_ids": list(self.missing_ids),
            "forced": self.forced,
            "reason": self.reason,
            "error": self.error,
            "provenance_status": self.provenance_status,
            "provenance_error": self.provenance_error,
            "transitions": [dict(transition) for transition in self.transitions],
        }


def _moment(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _selected_scope(
    store: MemoryEnvelopeStore,
    record_id: str,
    scope: MemoryScope | None,
) -> MemoryScope | None:
    return scope if scope is not None else store.scope_for_id(record_id)


def _from_store_result(operation: str, result: StoreResult) -> ForgetReport:
    pinned_count = max(0, len(result.skipped_ids) - len(result.scope_denied_ids))
    if result.status == "succeeded" and (result.skipped_ids or result.missing_ids):
        status = "partial"
        reason = "completed_with_refusals_or_missing_ids"
    elif result.status == "succeeded":
        status = "succeeded"
        reason = result.reason
    elif result.status == "skipped" and pinned_count:
        status = "refused"
        reason = "pinned_record_protected; pass force=True to override"
    elif result.status == "skipped" and result.scope_denied_ids:
        status = "refused"
        reason = "actor_scope_denied"
    else:
        status = result.status
        reason = result.reason
    return ForgetReport(
        status=status,
        operation=operation,
        requested=result.requested,
        changed=result.deleted,
        deleted=result.deleted,
        pinned_skipped=pinned_count,
        scope_denied=len(result.scope_denied_ids),
        record_ids=list(result.record_ids),
        missing_ids=list(result.missing_ids),
        forced=result.forced,
        reason=reason,
        error=result.error,
        provenance_status=result.provenance_status,
        provenance_error=result.provenance_error,
    )


def forget(
    record_id: str,
    *,
    store: MemoryEnvelopeStore,
    actor_scope: MemoryScope,
    scope: MemoryScope | None = None,
    reason: str = "explicit forget request",
    force: bool = False,
    now: float | None = None,
) -> ForgetReport:
    """Hard-delete one id, refusing a pinned record unless force is explicit."""

    if not store.enabled:
        return ForgetReport(status="skipped", operation="forget", requested=1, reason="disabled")
    selected_scope = _selected_scope(store, record_id, scope)
    if selected_scope is None:
        return ForgetReport(
            status="failed",
            operation="forget",
            requested=1,
            record_ids=[record_id],
            reason="not_found",
            error=f"memory {record_id!r} is not indexed in an exact scope",
        )
    result = store.remove(
        [record_id],
        scope=selected_scope,
        actor_scope=actor_scope,
        reason=reason,
        now=_moment(now),
        force=force,
    )
    return _from_store_result("forget", result)


def forget_scope(
    scope: MemoryScope,
    *,
    store: MemoryEnvelopeStore,
    actor_scope: MemoryScope,
    record_ids: list[str] | None = None,
    reason: str = "explicit scope forget request",
    force: bool = False,
    now: float | None = None,
) -> ForgetReport:
    """Hard-delete a whole exact scope (or a disclosed subset) in one write."""

    if not store.enabled:
        return ForgetReport(status="skipped", operation="forget_scope", reason="disabled")
    selected_ids = record_ids
    if selected_ids is None:
        selected_ids = store.ids_for_forget(scope, actor_scope=actor_scope)
    if not selected_ids:
        return ForgetReport(status="empty", operation="forget_scope", reason="no_visible_records")
    result = store.remove(
        selected_ids,
        scope=scope,
        actor_scope=actor_scope,
        reason=reason,
        now=_moment(now),
        force=force,
    )
    report = _from_store_result("forget_scope", result)
    report.requested = len(selected_ids)
    return report


def _transition_report(operation: str, result: TransitionResult, requested: int) -> ForgetReport:
    status_map = {
        "succeeded": "succeeded",
        "skipped": "skipped",
        "refused": "refused",
        "illegal_transition": "refused",
        "failed": "failed",
    }
    return ForgetReport(
        status=status_map.get(result.status, "failed"),
        operation=operation,
        requested=requested,
        changed=1 if result.ok else 0,
        record_ids=[result.envelope.id] if result.envelope is not None else [],
        forced=result.forced,
        reason=result.reason,
        error=result.error,
        provenance_status=result.provenance_status,
        provenance_error=result.provenance_error,
        transitions=[result.to_dict()],
    )


def archive(
    record_id: str,
    *,
    store: MemoryEnvelopeStore,
    actor_scope: MemoryScope,
    scope: MemoryScope | None = None,
    reason: str = "explicit archive request",
    force: bool = False,
    now: float | None = None,
) -> ForgetReport:
    """Archive through legal adjacent promotions and return every step."""

    if not store.enabled:
        return ForgetReport(status="skipped", operation="archive", requested=1, reason="disabled")
    selected_scope = _selected_scope(store, record_id, scope)
    if selected_scope is None:
        return ForgetReport(
            status="failed",
            operation="archive",
            requested=1,
            record_ids=[record_id],
            reason="not_found",
        )
    record = store.get(record_id, scope=selected_scope, reader_scope=actor_scope)
    if record is None:
        return ForgetReport(
            status="failed",
            operation="archive",
            requested=1,
            record_ids=[record_id],
            reason="not_found_or_scope_denied",
        )
    timestamp = _moment(now)
    if record.lifecycle.status is LifecycleStatus.ARCHIVED:
        return ForgetReport(
            status="succeeded",
            operation="archive",
            requested=1,
            record_ids=[record.id],
            reason="already_archived",
        )
    if record.lifecycle.status is LifecycleStatus.PURGED:
        return ForgetReport(
            status="refused",
            operation="archive",
            requested=1,
            record_ids=[record.id],
            reason="purged_record_cannot_be_archived",
        )
    targets = [LifecycleStatus.ARCHIVED]
    if record.lifecycle.status is LifecycleStatus.ACTIVE:
        targets.insert(0, LifecycleStatus.COMPRESSED)
    transition_results: list[TransitionResult] = []
    for target in targets:
        result = store.transition(
            record_id,
            target,
            scope=selected_scope,
            actor_scope=actor_scope,
            now=timestamp,
            reason=reason,
            force=force,
        )
        transition_results.append(result)
        if not result.ok:
            break
    final = transition_results[-1]
    changed = sum(result.ok for result in transition_results)
    if all(result.ok for result in transition_results):
        status = "succeeded"
    elif changed:
        status = "partial"
    else:
        status = "refused" if final.status in {"refused", "illegal_transition"} else final.status
    provenance_errors = [result.provenance_error for result in transition_results if result.provenance_error]
    return ForgetReport(
        status=status,
        operation="archive",
        requested=1,
        changed=changed,
        pinned_skipped=sum(result.status == "refused" and "pinned" in result.reason for result in transition_results),
        record_ids=[record_id],
        forced=any(result.forced for result in transition_results),
        reason=final.reason,
        error=final.error,
        provenance_status="failed" if provenance_errors else "written" if transition_results else "",
        provenance_error="; ".join(provenance_errors),
        transitions=[result.to_dict() for result in transition_results],
    )


def restore(
    record_id: str,
    *,
    store: MemoryEnvelopeStore,
    actor_scope: MemoryScope,
    scope: MemoryScope | None = None,
    reason: str = "explicit restore request",
    force: bool = False,
    now: float | None = None,
) -> ForgetReport:
    """Restore one compressed, archived, or lifecycle-purged envelope to active."""

    if not store.enabled:
        return ForgetReport(status="skipped", operation="restore", requested=1, reason="disabled")
    selected_scope = _selected_scope(store, record_id, scope)
    if selected_scope is None:
        return ForgetReport(
            status="failed",
            operation="restore",
            requested=1,
            record_ids=[record_id],
            reason="not_found",
        )
    result = store.restore(
        record_id,
        scope=selected_scope,
        actor_scope=actor_scope,
        now=_moment(now),
        reason=reason,
        force=force,
    )
    return _transition_report("restore", result, requested=1)


class ForgetService:
    """Convenience facade that binds one store and actor scope."""

    def __init__(self, store: MemoryEnvelopeStore, actor_scope: MemoryScope) -> None:
        self.store = store
        self.actor_scope = actor_scope

    def forget(
        self,
        record_id: str,
        *,
        scope: MemoryScope | None = None,
        reason: str = "explicit forget request",
        force: bool = False,
        now: float | None = None,
    ) -> ForgetReport:
        return forget(
            record_id,
            store=self.store,
            actor_scope=self.actor_scope,
            scope=scope,
            reason=reason,
            force=force,
            now=now,
        )

    def forget_scope(
        self,
        scope: MemoryScope,
        *,
        record_ids: list[str] | None = None,
        reason: str = "explicit scope forget request",
        force: bool = False,
        now: float | None = None,
    ) -> ForgetReport:
        return forget_scope(
            scope,
            store=self.store,
            actor_scope=self.actor_scope,
            record_ids=record_ids,
            reason=reason,
            force=force,
            now=now,
        )

    def archive(
        self,
        record_id: str,
        *,
        scope: MemoryScope | None = None,
        reason: str = "explicit archive request",
        force: bool = False,
        now: float | None = None,
    ) -> ForgetReport:
        return archive(
            record_id,
            store=self.store,
            actor_scope=self.actor_scope,
            scope=scope,
            reason=reason,
            force=force,
            now=now,
        )

    def restore(
        self,
        record_id: str,
        *,
        scope: MemoryScope | None = None,
        reason: str = "explicit restore request",
        force: bool = False,
        now: float | None = None,
    ) -> ForgetReport:
        return restore(
            record_id,
            store=self.store,
            actor_scope=self.actor_scope,
            scope=scope,
            reason=reason,
            force=force,
            now=now,
        )


__all__ = [
    "ForgetReport",
    "ForgetService",
    "archive",
    "forget",
    "forget_scope",
    "restore",
]
