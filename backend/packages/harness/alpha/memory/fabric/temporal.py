"""Bi-temporal helpers for canonical memory validity and contradiction links."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .models import MemoryEnvelope, Timestamps


@dataclass(slots=True)
class TemporalResult:
    """Disclosed result of a temporal link mutation."""

    status: str
    old: MemoryEnvelope | None = None
    new: MemoryEnvelope | None = None
    changed: bool = False
    reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether both returned envelopes are new validated copies."""

        return self.status == "succeeded" and self.changed and self.old is not None and self.new is not None

    def to_dict(self) -> dict[str, object]:
        """Serialize the result without losing refusal details."""

        return {
            "status": self.status,
            "old": self.old.to_dict() if self.old is not None else None,
            "new": self.new.to_dict() if self.new is not None else None,
            "changed": self.changed,
            "reason": self.reason,
            "error": self.error,
        }


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _with_timestamps(envelope: MemoryEnvelope, timestamps: Timestamps) -> MemoryEnvelope:
    payload = envelope.model_dump()
    payload["timestamps"] = timestamps
    return MemoryEnvelope.model_validate(payload)


def assert_temporal_integrity(
    records: MemoryEnvelope | Iterable[MemoryEnvelope],
    *additional: MemoryEnvelope | Iterable[MemoryEnvelope],
) -> None:
    """Raise ``ValueError`` for invalid intervals, duplicate ids, or bad links.

    Overlapping intervals are valid because unresolved contradictions and bad
    source merges must preserve both alternatives.  When both sides of a
    supersession link are present, this function additionally requires the
    reciprocal link and an exact shared boundary.
    """

    raw_candidates = (records, *additional)
    candidates: list[MemoryEnvelope] = []
    for candidate in raw_candidates:
        if isinstance(candidate, MemoryEnvelope):
            candidates.append(candidate)
        else:
            candidates.extend(candidate)
    by_id: dict[str, MemoryEnvelope] = {}
    for envelope in candidates:
        if not isinstance(envelope, MemoryEnvelope):
            raise ValueError("temporal integrity requires MemoryEnvelope values")
        if envelope.id in by_id:
            raise ValueError(f"duplicate memory id {envelope.id!r}")
        by_id[envelope.id] = envelope
        valid_from = envelope.timestamps.valid_from
        if valid_from is None:
            raise ValueError(f"memory {envelope.id!r} has no valid_from")
        if envelope.timestamps.valid_to is not None and envelope.timestamps.valid_to < valid_from:
            raise ValueError(f"memory {envelope.id!r} has valid_to before valid_from")

    for envelope in candidates:
        for predecessor_id in envelope.lifecycle.supersedes:
            predecessor = by_id.get(predecessor_id)
            if predecessor is not None:
                if envelope.id not in predecessor.lifecycle.superseded_by:
                    raise ValueError(f"supersession link {predecessor.id!r}->{envelope.id!r} is not reciprocal")
                if predecessor.timestamps.valid_to != envelope.timestamps.valid_from:
                    raise ValueError(f"supersession boundary mismatch between {predecessor.id!r} and {envelope.id!r}")
        for successor_id in envelope.lifecycle.superseded_by:
            successor = by_id.get(successor_id)
            if successor is not None:
                if envelope.id not in successor.lifecycle.supersedes:
                    raise ValueError(f"supersession link {envelope.id!r}->{successor_id!r} is not reciprocal")
                if envelope.timestamps.valid_to != successor.timestamps.valid_from:
                    raise ValueError(f"supersession boundary mismatch between {envelope.id!r} and {successor.id!r}")


def supersede(
    old: MemoryEnvelope,
    new: MemoryEnvelope,
    now: float,
) -> TemporalResult:
    """Close ``old`` and open ``new`` at ``now`` with reciprocal links.

    A replacement whose declared ``valid_from`` predates the old version is
    refused rather than rewriting history backwards.  On success the two
    supplied envelopes are updated in place and also returned in the disclosed
    result; a refusal leaves both inputs untouched.
    """

    if not isinstance(old, MemoryEnvelope) or not isinstance(new, MemoryEnvelope):
        return TemporalResult(status="failed", reason="invalid_envelope", error="expected two MemoryEnvelope values")
    if old.id == new.id:
        return TemporalResult(
            status="refused",
            old=old,
            new=new,
            reason="self_supersession_refused",
        )
    if old.scope != new.scope:
        return TemporalResult(
            status="refused",
            old=old,
            new=new,
            reason="scope_mismatch",
        )
    try:
        moment = _finite(now, "now")
    except (TypeError, ValueError) as exc:
        return TemporalResult(status="failed", old=old, new=new, reason="invalid_timestamp", error=str(exc))
    old_valid_from = old.timestamps.valid_from
    new_valid_from = new.timestamps.valid_from
    if old_valid_from is None or new_valid_from is None:
        return TemporalResult(status="refused", old=old, new=new, reason="missing_valid_from")
    if new_valid_from < old_valid_from:
        return TemporalResult(
            status="refused",
            old=old,
            new=new,
            reason="replacement_valid_from_precedes_existing",
        )
    if moment < old_valid_from:
        return TemporalResult(
            status="refused",
            old=old,
            new=new,
            reason="supersession_time_precedes_existing",
        )
    if moment < new_valid_from:
        return TemporalResult(
            status="refused",
            old=old,
            new=new,
            reason="supersession_time_precedes_replacement",
        )
    if new.timestamps.valid_to is not None and new.timestamps.valid_to < moment:
        return TemporalResult(
            status="refused",
            old=old,
            new=new,
            reason="replacement_interval_already_closed",
        )
    try:
        old_timestamp_payload = old.timestamps.model_dump()
        old_timestamp_payload["valid_to"] = moment
        new_timestamp_payload = new.timestamps.model_dump()
        new_timestamp_payload["valid_from"] = moment
        updated_old = _with_timestamps(old, Timestamps.model_validate(old_timestamp_payload))
        new_links = list(new.lifecycle.supersedes)
        if old.id not in new_links:
            new_links.append(old.id)
        successor_links = list(updated_old.lifecycle.superseded_by)
        if new.id not in successor_links:
            successor_links.append(new.id)
        new_lifecycle = new.lifecycle.model_copy(deep=True)
        new_lifecycle.supersedes = new_links
        new_payload = new.model_dump()
        new_payload["timestamps"] = Timestamps.model_validate(new_timestamp_payload)
        new_payload["lifecycle"] = new_lifecycle
        updated_new = MemoryEnvelope.model_validate(new_payload)
        old_payload = updated_old.model_dump()
        old_lifecycle = updated_old.lifecycle.model_copy(deep=True)
        old_lifecycle.superseded_by = successor_links
        old_payload["lifecycle"] = old_lifecycle
        updated_old = MemoryEnvelope.model_validate(old_payload)
    except (TypeError, ValueError) as exc:
        return TemporalResult(status="failed", old=old, new=new, reason="temporal_validation_failed", error=str(exc))
    object.__setattr__(old, "timestamps", updated_old.timestamps)
    object.__setattr__(old, "lifecycle", updated_old.lifecycle)
    object.__setattr__(new, "timestamps", updated_new.timestamps)
    object.__setattr__(new, "lifecycle", updated_new.lifecycle)
    return TemporalResult(
        status="succeeded",
        old=old,
        new=new,
        changed=True,
        reason="new version supersedes old version at exact shared boundary",
    )


def _append_link(existing: list[str], value: str) -> list[str]:
    links = list(existing)
    if value not in links:
        links.append(value)
    return links


def contradict(first: MemoryEnvelope, second: MemoryEnvelope) -> TemporalResult:
    """Link two retained alternatives without silently resolving either one.

    Successful links are applied to both supplied envelopes; a refusal leaves
    them unchanged.
    """

    if not isinstance(first, MemoryEnvelope) or not isinstance(second, MemoryEnvelope):
        return TemporalResult(status="failed", reason="invalid_envelope", error="expected two MemoryEnvelope values")
    if first.id == second.id:
        return TemporalResult(
            status="refused",
            old=first,
            new=second,
            reason="self_contradiction_refused",
        )
    if first.scope != second.scope:
        return TemporalResult(
            status="refused",
            old=first,
            new=second,
            reason="scope_mismatch",
        )
    try:
        first_lifecycle = first.lifecycle.model_copy(deep=True)
        first_lifecycle.contradicts = _append_link(first_lifecycle.contradicts, second.id)
        second_lifecycle = second.lifecycle.model_copy(deep=True)
        second_lifecycle.contradicts = _append_link(second_lifecycle.contradicts, first.id)
        first_payload = first.model_dump()
        first_payload["lifecycle"] = first_lifecycle
        second_payload = second.model_dump()
        second_payload["lifecycle"] = second_lifecycle
        updated_first = MemoryEnvelope.model_validate(first_payload)
        updated_second = MemoryEnvelope.model_validate(second_payload)
    except (TypeError, ValueError) as exc:
        return TemporalResult(status="failed", old=first, new=second, reason="contradiction_validation_failed", error=str(exc))
    object.__setattr__(first, "lifecycle", updated_first.lifecycle)
    object.__setattr__(second, "lifecycle", updated_second.lifecycle)
    return TemporalResult(
        status="succeeded",
        old=first,
        new=second,
        changed=True,
        reason="unresolved contradiction retained on both sides",
    )


def _deterministic_key(envelope: MemoryEnvelope) -> tuple[float, float, str]:
    valid_from = envelope.timestamps.valid_from
    if valid_from is None:
        valid_from = envelope.timestamps.created_at
    return (-valid_from, -envelope.timestamps.created_at, envelope.id)


def as_of(store_records: Iterable[MemoryEnvelope], when: float) -> list[MemoryEnvelope]:
    """Return every non-purged version true at ``when`` in deterministic order.

    Intervals are closed for compatibility with Alpha's existing
    spatio-temporal helper, so adjacent versions both appear at an exact change
    boundary.  Ordering then prefers the latest ``valid_from``, creation time,
    and lexical id, making overlaps deterministic without discarding conflict
    alternatives.
    """

    try:
        moment = _finite(when, "when")
    except (TypeError, ValueError):
        return []
    selected = [record for record in store_records if isinstance(record, MemoryEnvelope) and record.as_of(moment) is not None]
    selected.sort(key=_deterministic_key)
    return [record.model_copy(deep=True) for record in selected]


def latest(records: Iterable[MemoryEnvelope], when: float | None = None) -> MemoryEnvelope | None:
    """Return the deterministic newest valid version, or ``None``."""

    if when is None:
        import time

        moment = time.time()
    else:
        try:
            moment = _finite(when, "when")
        except (TypeError, ValueError):
            return None
    candidates = as_of(records, moment)
    return candidates[0] if candidates else None


__all__ = [
    "TemporalResult",
    "as_of",
    "assert_temporal_integrity",
    "contradict",
    "latest",
    "supersede",
]
