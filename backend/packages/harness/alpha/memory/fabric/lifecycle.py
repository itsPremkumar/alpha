"""Pure lifecycle state machine for canonical memory envelopes.

Transitions are deliberately pure: callers receive a new envelope and an
explicit result, and persistence/audit side effects belong to the store.  The
adjacent-state graph follows plan sections 3.4, 3.5, 18, and 31.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .config import FabricConfig
from .models import Lifecycle, LifecycleStatus, MemoryEnvelope

PROMOTION_ORDER = (
    LifecycleStatus.ACTIVE,
    LifecycleStatus.COMPRESSED,
    LifecycleStatus.ARCHIVED,
    LifecycleStatus.PURGED,
)

#: Only adjacent promotion/demotion edges are legal ordinary transitions.
LEGAL_TRANSITIONS: dict[LifecycleStatus, frozenset[LifecycleStatus]] = {
    LifecycleStatus.ACTIVE: frozenset({LifecycleStatus.COMPRESSED}),
    LifecycleStatus.COMPRESSED: frozenset({LifecycleStatus.ACTIVE, LifecycleStatus.ARCHIVED}),
    LifecycleStatus.ARCHIVED: frozenset({LifecycleStatus.COMPRESSED, LifecycleStatus.PURGED}),
    LifecycleStatus.PURGED: frozenset({LifecycleStatus.ARCHIVED}),
}
VALID_TRANSITIONS = LEGAL_TRANSITIONS


@dataclass(frozen=True, slots=True)
class Transition:
    """One proposed lifecycle step, as returned by the age policy."""

    target: LifecycleStatus
    reason: str
    automatic: bool = False
    force: bool = False


@dataclass(slots=True)
class TransitionResult:
    """Disclosed outcome of one pure transition attempt."""

    status: str
    envelope: MemoryEnvelope | None = None
    from_status: LifecycleStatus | None = None
    to_status: LifecycleStatus | None = None
    changed: bool = False
    reason: str = ""
    error: str = ""
    forced: bool = False
    automatic: bool = False
    provenance_status: str = ""
    provenance_error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether a new lifecycle state was produced."""

        return self.status == "succeeded" and self.changed and self.envelope is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize both the outcome and any resulting canonical envelope."""

        return {
            "status": self.status,
            "envelope": self.envelope.to_dict() if self.envelope is not None else None,
            "from_status": self.from_status.value if self.from_status is not None else None,
            "to_status": self.to_status.value if self.to_status is not None else None,
            "changed": self.changed,
            "reason": self.reason,
            "error": self.error,
            "forced": self.forced,
            "automatic": self.automatic,
            "provenance_status": self.provenance_status,
            "provenance_error": self.provenance_error,
            "details": dict(self.details),
        }


def _rebuild(envelope: MemoryEnvelope, lifecycle: Lifecycle) -> MemoryEnvelope:
    payload = envelope.model_dump()
    payload["lifecycle"] = lifecycle
    return MemoryEnvelope.model_validate(payload)


def _timestamp(now: float | None) -> float:
    value = time.time() if now is None else float(now)
    if not math.isfinite(value):
        raise ValueError("transition timestamp must be finite")
    return value


def _normalized_status(value: LifecycleStatus | str) -> LifecycleStatus | None:
    if isinstance(value, LifecycleStatus):
        return value
    try:
        return LifecycleStatus(str(value).strip().lower())
    except ValueError:
        return None


def transition_allowed(current: LifecycleStatus | str, target: LifecycleStatus | str) -> bool:
    """Return whether the closed graph permits an ordinary adjacent edge."""

    current_status = _normalized_status(current)
    target_status = _normalized_status(target)
    if current_status is None or target_status is None:
        return False
    return target_status in LEGAL_TRANSITIONS.get(current_status, frozenset())


def transition(
    envelope: MemoryEnvelope,
    target: LifecycleStatus | str,
    *,
    now: float | None = None,
    reason: str = "",
    force: bool = False,
    automatic: bool = False,
) -> TransitionResult:
    """Apply one legal adjacent promotion or demotion without mutating input."""

    if not isinstance(envelope, MemoryEnvelope):
        return TransitionResult(status="failed", reason="invalid_envelope", error="expected MemoryEnvelope")
    target_status = _normalized_status(target)
    if target_status is None:
        return TransitionResult(
            status="refused",
            envelope=envelope,
            from_status=envelope.lifecycle.status,
            reason="unknown_target_status",
            error=f"unknown lifecycle target {target!r}",
        )
    source_status = envelope.lifecycle.status
    if source_status == target_status:
        return TransitionResult(
            status="skipped",
            envelope=envelope,
            from_status=source_status,
            to_status=target_status,
            reason=f"already_{target_status.value}",
            forced=force,
            automatic=automatic,
        )
    if not transition_allowed(source_status, target_status):
        return TransitionResult(
            status="illegal_transition",
            envelope=envelope,
            from_status=source_status,
            to_status=target_status,
            reason=f"cannot transition {source_status.value} to {target_status.value}; only adjacent promotion/demotion is legal",
        )
    if envelope.lifecycle.pinned and not force:
        return TransitionResult(
            status="refused",
            envelope=envelope,
            from_status=source_status,
            to_status=target_status,
            reason="pinned_record_protected; pass force=True to override",
        )
    try:
        _timestamp(now)
        lifecycle = envelope.lifecycle.model_copy(deep=True)
        lifecycle.status = target_status
        updated = _rebuild(envelope, lifecycle)
    except (TypeError, ValueError) as exc:
        return TransitionResult(
            status="failed",
            envelope=envelope,
            from_status=source_status,
            to_status=target_status,
            reason="transition_validation_failed",
            error=str(exc),
        )
    return TransitionResult(
        status="succeeded",
        envelope=updated,
        from_status=source_status,
        to_status=target_status,
        changed=True,
        reason=str(reason or f"transition to {target_status.value}"),
        forced=force,
        automatic=automatic,
    )


def promote(
    envelope: MemoryEnvelope,
    *,
    now: float | None = None,
    reason: str = "",
    force: bool = False,
    automatic: bool = False,
) -> TransitionResult:
    """Promote an envelope exactly one lifecycle step."""

    source_status = envelope.lifecycle.status
    index = PROMOTION_ORDER.index(source_status)
    if index >= len(PROMOTION_ORDER) - 1:
        return TransitionResult(
            status="skipped",
            envelope=envelope,
            from_status=source_status,
            to_status=source_status,
            reason="already_purged",
            forced=force,
            automatic=automatic,
        )
    return transition(
        envelope,
        PROMOTION_ORDER[index + 1],
        now=now,
        reason=reason,
        force=force,
        automatic=automatic,
    )


def demote(
    envelope: MemoryEnvelope,
    *,
    now: float | None = None,
    reason: str = "",
    force: bool = False,
    automatic: bool = False,
) -> TransitionResult:
    """Demote an envelope exactly one lifecycle step."""

    source_status = envelope.lifecycle.status
    index = PROMOTION_ORDER.index(source_status)
    if index <= 0:
        return TransitionResult(
            status="skipped",
            envelope=envelope,
            from_status=source_status,
            to_status=source_status,
            reason="already_active",
            forced=force,
            automatic=automatic,
        )
    return transition(
        envelope,
        PROMOTION_ORDER[index - 1],
        now=now,
        reason=reason,
        force=force,
        automatic=automatic,
    )


def restore_envelope(
    envelope: MemoryEnvelope,
    *,
    now: float | None = None,
    reason: str = "explicit restore",
    force: bool = False,
) -> TransitionResult:
    """Explicitly restore any non-active envelope to ACTIVE.

    Restore is intentionally not an ordinary adjacent edge: it is a visible
    operator action that may cross several compressed/archive representations.
    """

    if not isinstance(envelope, MemoryEnvelope):
        return TransitionResult(status="failed", reason="invalid_envelope", error="expected MemoryEnvelope")
    source_status = envelope.lifecycle.status
    if source_status is LifecycleStatus.ACTIVE:
        return TransitionResult(
            status="skipped",
            envelope=envelope,
            from_status=source_status,
            to_status=source_status,
            reason="already_active",
            forced=force,
        )
    if envelope.lifecycle.pinned and not force:
        return TransitionResult(
            status="refused",
            envelope=envelope,
            from_status=source_status,
            to_status=LifecycleStatus.ACTIVE,
            reason="pinned_record_protected; pass force=True to override",
        )
    try:
        _timestamp(now)
        lifecycle = envelope.lifecycle.model_copy(deep=True)
        lifecycle.status = LifecycleStatus.ACTIVE
        updated = _rebuild(envelope, lifecycle)
    except (TypeError, ValueError) as exc:
        return TransitionResult(
            status="failed",
            envelope=envelope,
            from_status=source_status,
            to_status=LifecycleStatus.ACTIVE,
            reason="restore_validation_failed",
            error=str(exc),
        )
    return TransitionResult(
        status="succeeded",
        envelope=updated,
        from_status=source_status,
        to_status=LifecycleStatus.ACTIVE,
        changed=True,
        reason=reason,
        forced=force,
        details={"explicit_restore": True},
    )


def due_transitions(
    envelope: MemoryEnvelope,
    now: float,
    config: FabricConfig | Mapping[str, object],
) -> list[Transition]:
    """Return every missing sequential promotion due at an exact age boundary.

    The input envelope is never mutated.  Pinned and already-purged records
    return no proposals.  When an old ACTIVE record is beyond several age
    thresholds, the returned list is the legal shortest path to its due state.
    """

    if not isinstance(envelope, MemoryEnvelope):
        return []
    try:
        effective_config = config if isinstance(config, FabricConfig) else FabricConfig.model_validate(config)
    except (TypeError, ValueError):
        return []
    moment = float(now)
    if not math.isfinite(moment) or moment < envelope.timestamps.created_at:
        return []
    if envelope.lifecycle.pinned or envelope.lifecycle.status is LifecycleStatus.PURGED:
        return []

    age_days = max(0.0, (moment - envelope.timestamps.created_at) / 86_400.0)
    ttl_due = False
    if envelope.lifecycle.ttl_seconds is not None:
        ttl_due = moment >= envelope.timestamps.created_at + envelope.lifecycle.ttl_seconds
    ttl_due = ttl_due or envelope.is_expired(moment)

    if ttl_due:
        target_rank = len(PROMOTION_ORDER) - 1
        target_reason = "retention TTL/expires_at boundary reached"
    elif age_days >= effective_config.purge_after_days:
        target_rank = len(PROMOTION_ORDER) - 1
        target_reason = f"purge_after_days={effective_config.purge_after_days:g} reached"
    elif age_days >= effective_config.archive_after_days:
        target_rank = PROMOTION_ORDER.index(LifecycleStatus.ARCHIVED)
        target_reason = f"archive_after_days={effective_config.archive_after_days:g} reached"
    elif age_days >= effective_config.compress_after_days:
        target_rank = PROMOTION_ORDER.index(LifecycleStatus.COMPRESSED)
        target_reason = f"compress_after_days={effective_config.compress_after_days:g} reached"
    else:
        return []

    current_rank = PROMOTION_ORDER.index(envelope.lifecycle.status)
    if target_rank <= current_rank:
        return []
    return [
        Transition(
            target=PROMOTION_ORDER[index],
            reason=target_reason,
            automatic=True,
        )
        for index in range(current_rank + 1, target_rank + 1)
    ]


# Module alias retained for callers that import the lifecycle module directly.
restore = restore_envelope


__all__ = [
    "LEGAL_TRANSITIONS",
    "PROMOTION_ORDER",
    "VALID_TRANSITIONS",
    "Transition",
    "TransitionResult",
    "demote",
    "due_transitions",
    "promote",
    "restore",
    "restore_envelope",
    "transition",
    "transition_allowed",
]
