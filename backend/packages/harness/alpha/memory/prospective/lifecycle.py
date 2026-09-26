"""Pure lifecycle transitions for prospective-memory records.

These functions never perform filesystem or network I/O.  The store applies a
transition atomically, writes provenance, and returns the same
:class:`LifecycleResult` shape when persistence fails.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .config import ProspectiveConfig
from .models import (
    LifecycleResult,
    ProspectiveItem,
    ProspectiveKind,
    ProspectiveStatus,
    TriggerSpec,
    can_transition,
    new_item_id,
)


def _timestamp(now: float | None) -> float:
    value = time.time() if now is None else float(now)
    if not math.isfinite(value):
        raise ValueError("timestamp must be finite")
    return value


def _copy_with(item: ProspectiveItem, **updates: Any) -> ProspectiveItem:
    payload = item.model_dump()
    payload.update(updates)
    return ProspectiveItem.model_validate(payload)


def _coerce_item(item: ProspectiveItem | Mapping[str, Any] | LifecycleResult) -> ProspectiveItem | None:
    if isinstance(item, LifecycleResult):
        item = item.item
    if item is None:
        return None
    if isinstance(item, ProspectiveItem):
        return item
    try:
        return ProspectiveItem.model_validate(dict(item))
    except (TypeError, ValueError):
        return None


def _failure(reason: str, error: str = "", *, item: ProspectiveItem | None = None) -> LifecycleResult:
    return LifecycleResult(status="failed", item=item, reason=reason, error=error or reason)


def _illegal(item: ProspectiveItem, target: ProspectiveStatus) -> LifecycleResult:
    return LifecycleResult(
        status="illegal_transition",
        item=item,
        reason=f"cannot transition {item.status.value} to {target.value}",
    )


def _transition(
    item: ProspectiveItem,
    target: ProspectiveStatus,
    *,
    now: float | None,
    reason: str,
) -> LifecycleResult:
    normalized = _coerce_item(item)
    if normalized is None:
        return _failure("item_validation_failed", "lifecycle item is not a valid ProspectiveItem")
    item = normalized
    if item.status == target:
        return LifecycleResult(
            status="skipped",
            item=item,
            reason=f"already_{target.value}",
        )
    if not can_transition(item.status, target):
        return _illegal(item, target)
    try:
        timestamp = _timestamp(now)
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("timestamp_invalid", str(exc), item=item)
    updates: dict[str, Any] = {
        "status": target,
        "updated_at": timestamp,
        "last_reason": reason,
    }
    if target is ProspectiveStatus.DONE:
        updates["completed_at"] = timestamp
    elif target is ProspectiveStatus.CANCELLED:
        updates["cancelled_at"] = timestamp
    elif target is ProspectiveStatus.FIRED:
        updates["fired_at"] = timestamp
    try:
        updated = _copy_with(item, **updates)
    except (ValidationError, TypeError, ValueError) as exc:
        return _failure("transition_validation_failed", str(exc), item=item)
    return LifecycleResult(
        status="succeeded",
        item=updated,
        changed=True,
        event=target.value,
        reason=reason,
    )


def create_item(
    content: str = "",
    *,
    user_id: str | None = None,
    kind: ProspectiveKind | str = ProspectiveKind.REMINDER,
    agent_name: str | None = None,
    priority: int | None = None,
    due_at: float | None = None,
    trigger: TriggerSpec | Mapping[str, Any] | None = None,
    source: str | dict[str, Any] | None = None,
    linked_record_ids: list[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: float | None = None,
    item_id: str | None = None,
    id: str | None = None,
    grace_until: float | None = None,
    recurrence_interval: float | None = None,
    recurring_max_occurrences: int | None = None,
    occurrence: int = 1,
    config: ProspectiveConfig | None = None,
    item: ProspectiveItem | None = None,
) -> LifecycleResult:
    """Construct a new item without I/O or an enable-gate side effect.

    The store owns the gate so this pure constructor remains useful to
    migration/import code.  ``item`` can be supplied when a caller already
    has a fully validated record; otherwise all defaults come from the
    injected :class:`ProspectiveConfig`.
    """

    if isinstance(item, LifecycleResult):
        item = item.item
    if item is not None:
        return LifecycleResult(status="succeeded", item=item.model_copy(deep=True), changed=True, event="created")
    effective_config = config if config is not None else ProspectiveConfig()
    try:
        timestamp = _timestamp(now)
        item_metadata = dict(metadata or {})
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("item_metadata_invalid", str(exc))
    if recurrence_interval is None:
        for key in ("recurrence_interval", "interval_seconds", "cadence_seconds"):
            candidate = item_metadata.get(key)
            if candidate is not None:
                try:
                    recurrence_interval = float(candidate)
                except (TypeError, ValueError, OverflowError) as exc:
                    return _failure("recurrence_interval_invalid", str(exc))
                break
    if recurring_max_occurrences is None:
        candidate = item_metadata.get("recurring_max_occurrences")
        if candidate is not None:
            try:
                recurring_max_occurrences = int(candidate)
            except (TypeError, ValueError, OverflowError) as exc:
                return _failure("recurring_max_occurrences_invalid", str(exc))
    if recurring_max_occurrences is None:
        recurring_max_occurrences = effective_config.recurring_max_occurrences
    if grace_until is None and due_at is not None:
        try:
            grace_until = float(due_at) + effective_config.expiry_grace_hours * 3600.0
        except (TypeError, ValueError, OverflowError) as exc:
            return _failure("grace_until_invalid", str(exc))
    selected_priority = effective_config.default_priority if priority is None else priority
    try:
        trigger_value = None if trigger is None else TriggerSpec.model_validate(trigger)
        created = ProspectiveItem(
            id=item_id or id or new_item_id(),
            user_id=user_id or "default",
            agent_name=agent_name,
            kind=kind,
            content=content,
            priority=selected_priority,
            due_at=due_at,
            trigger=trigger_value,
            created_at=timestamp,
            updated_at=timestamp,
            grace_until=grace_until,
            source=source,
            linked_record_ids=list(linked_record_ids or []),
            metadata=item_metadata,
            occurrence=occurrence,
            recurrence_interval=recurrence_interval,
            recurring_max_occurrences=recurring_max_occurrences,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        return _failure("item_validation_failed", str(exc))
    return LifecycleResult(status="succeeded", item=created, changed=True, event="created")


def cancel(
    item: ProspectiveItem,
    *,
    now: float | None = None,
    reason: str = "cancelled by request",
) -> LifecycleResult:
    """Cancel a pending or fired item; terminal transitions are disclosed."""

    return _transition(item, ProspectiveStatus.CANCELLED, now=now, reason=reason)


def complete(
    item: ProspectiveItem,
    *,
    now: float | None = None,
    reason: str = "completed",
) -> LifecycleResult:
    """Complete a pending or fired item."""

    return _transition(item, ProspectiveStatus.DONE, now=now, reason=reason)


def fire(
    item: ProspectiveItem,
    *,
    now: float | None = None,
    reason: str = "trigger fired",
) -> LifecycleResult:
    """Mark a pending item as fired."""

    return _transition(item, ProspectiveStatus.FIRED, now=now, reason=reason)


# ``mark_fired`` is the lifecycle verb used by trigger-oriented callers.
mark_fired = fire


def mark_surfaced(
    item: ProspectiveItem,
    *,
    now: float | None = None,
    reason: str = "surfaced to the agent",
) -> LifecycleResult:
    """Record the first prompt surface for an item.

    A second call is a disclosed no-op, which prevents repeated recall from
    turning one reminder into an unbounded stream of duplicate obligations.
    """

    normalized = _coerce_item(item)
    if normalized is None:
        return _failure("item_validation_failed", "lifecycle item is not a valid ProspectiveItem")
    item = normalized
    if not item.is_active:
        return _illegal(item, ProspectiveStatus.PENDING)
    if item.surfaced_at is not None:
        return LifecycleResult(status="skipped", item=item, reason="already_surfaced")
    try:
        timestamp = _timestamp(now)
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("timestamp_invalid", str(exc), item=item)
    try:
        updated = _copy_with(
            item,
            surfaced_at=timestamp,
            updated_at=timestamp,
            last_reason=reason,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        return _failure("surfaced_validation_failed", str(exc), item=item)
    return LifecycleResult(status="succeeded", item=updated, changed=True, event="surfaced", reason=reason)


def expire_due(
    item: ProspectiveItem,
    *,
    now: float | None = None,
    reason: str = "due grace window elapsed",
) -> LifecycleResult:
    """Expire an active item only after its due/grace boundary has passed."""

    normalized = _coerce_item(item)
    if normalized is None:
        return _failure("item_validation_failed", "lifecycle item is not a valid ProspectiveItem")
    item = normalized
    if not item.is_active:
        return LifecycleResult(status="skipped", item=item, reason="not_active")
    try:
        timestamp = _timestamp(now)
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("timestamp_invalid", str(exc), item=item)
    if not item.is_due_at(timestamp):
        return LifecycleResult(status="skipped", item=item, reason="not_due")
    if not item.is_expired_at(timestamp):
        return LifecycleResult(status="skipped", item=item, reason="within_grace")
    return _transition(item, ProspectiveStatus.EXPIRED, now=timestamp, reason=reason)


def expand_recurring(
    item: ProspectiveItem,
    *,
    now: float | None = None,
    config: ProspectiveConfig | None = None,
    max_occurrences: int | None = None,
) -> LifecycleResult:
    """Create the next bounded occurrence of a recurring item.

    The returned ``item`` and ``next_item`` are the same new pending record;
    the source item remains unchanged.  Occurrence limits are hard caps, not
    an invitation to create an unbounded chain.
    """

    normalized = _coerce_item(item)
    if normalized is None:
        return _failure("item_validation_failed", "lifecycle item is not a valid ProspectiveItem")
    item = normalized
    effective_config = config if config is not None else ProspectiveConfig()
    if item.kind is not ProspectiveKind.RECURRING:
        return LifecycleResult(status="skipped", item=item, reason="not_recurring")
    limit = max_occurrences or item.recurring_max_occurrences or effective_config.recurring_max_occurrences
    if item.occurrence >= limit:
        return LifecycleResult(
            status="skipped",
            item=item,
            reason="recurring_max_occurrences",
            current=item.occurrence,
            limit=limit,
        )
    interval = item.recurrence_interval
    if interval is None:
        for key in ("recurrence_interval", "interval_seconds", "cadence_seconds"):
            candidate = item.metadata.get(key)
            if candidate is not None:
                try:
                    interval = float(candidate)
                except (TypeError, ValueError):
                    interval = None
                if interval is not None:
                    break
    if interval is None or interval <= 0:
        return _failure("recurrence_interval_missing", item=item)
    try:
        timestamp = _timestamp(now)
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("timestamp_invalid", str(exc), item=item)
    due_at = item.due_at + interval if item.due_at is not None else timestamp + interval
    next_metadata = dict(item.metadata)
    next_metadata["parent_item_id"] = item.id
    try:
        next_item = ProspectiveItem.model_validate(
            {
                **item.model_dump(),
                "id": new_item_id(),
                "status": ProspectiveStatus.PENDING,
                "created_at": timestamp,
                "updated_at": timestamp,
                "surfaced_at": None,
                "completed_at": None,
                "fired_at": None,
                "cancelled_at": None,
                "grace_until": due_at + effective_config.expiry_grace_hours * 3600.0,
                "occurrence": item.occurrence + 1,
                "recurring_max_occurrences": limit,
                "last_reason": "expanded recurring occurrence",
                "metadata": next_metadata,
            }
        )
    except (ValidationError, TypeError, ValueError) as exc:
        return _failure("recurring_validation_failed", str(exc), item=item)
    return LifecycleResult(
        status="succeeded",
        item=next_item,
        next_item=next_item,
        changed=True,
        event="created",
        reason="recurring occurrence expanded",
        current=next_item.occurrence,
        limit=limit,
    )


# Explicit name for callers that prefer the noun form.
expand_recurring_item = expand_recurring


def transition_allowed(current: ProspectiveStatus | str, target: ProspectiveStatus | str) -> bool:
    """Public wrapper around the closed transition table."""

    return can_transition(current, target)


__all__ = [
    "cancel",
    "complete",
    "create_item",
    "expand_recurring",
    "expand_recurring_item",
    "expire_due",
    "fire",
    "mark_fired",
    "mark_surfaced",
    "transition_allowed",
]
