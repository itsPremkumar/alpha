"""Feedback-signal normalization with explicit aliases and idempotency.

Providers call this plane with slightly different names (``click`` versus
``clicked``, ``timestamp`` versus ``observed_at``).  The override table is
intentional and closed: an unknown label is rejected rather than guessed.
Accepted observation ids are remembered per normalizer, so replaying a provider
delivery cannot inflate a score.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from .models import SignalOutcome, SignalStatus, UtilityEvent, UtilityObservation

#: Explicit provider-label overrides.  Canonical event names are included so a
#: caller can inspect the complete accepted vocabulary in one place.
EVENT_OVERRIDES: dict[str, UtilityEvent] = {
    "surface": UtilityEvent.SURFACED,
    "surfaced": UtilityEvent.SURFACED,
    "show": UtilityEvent.SURFACED,
    "shown": UtilityEvent.SURFACED,
    "click": UtilityEvent.CLICKED,
    "clicked": UtilityEvent.CLICKED,
    "open": UtilityEvent.CLICKED,
    "opened": UtilityEvent.CLICKED,
    "confirm": UtilityEvent.CONFIRMED,
    "confirmed": UtilityEvent.CONFIRMED,
    "explicit_confirmation": UtilityEvent.CONFIRMED,
    "contradict": UtilityEvent.CONTRADICTED,
    "contradicted": UtilityEvent.CONTRADICTED,
    "conflict": UtilityEvent.CONTRADICTED,
    "not_used": UtilityEvent.UNUSED,
    "unused": UtilityEvent.UNUSED,
    "ignore": UtilityEvent.UNUSED,
    "ignored": UtilityEvent.UNUSED,
    "recall": UtilityEvent.RECALLED,
    "recalled": UtilityEvent.RECALLED,
    "use": UtilityEvent.RECALLED,
    "used": UtilityEvent.RECALLED,
}

# A public alias makes the seam easy to discover without hiding the table.
OVERRIDE_MAP = EVENT_OVERRIDES


def _field(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def _timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("timestamp must be numeric or ISO-8601, not boolean")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, datetime):
        result = value.timestamp()
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp must not be blank")
        try:
            result = float(text)
        except ValueError:
            normalized = text.replace("Z", "+00:00")
            result = datetime.fromisoformat(normalized).timestamp()
    else:
        raise ValueError("timestamp must be numeric or ISO-8601")
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("timestamp must be finite and non-negative")
    return result


def _clock_value(clock: Any) -> float:
    if clock is None:
        raise LookupError("clock_unavailable")
    try:
        value = clock() if callable(clock) else getattr(clock, "now", lambda: None)()
    except (AttributeError, TypeError, ValueError, OverflowError, RuntimeError) as exc:
        raise LookupError(f"clock_unavailable:{exc}") from exc
    if value is None:
        raise LookupError("clock_returned_no_timestamp")
    return _timestamp(value)


def _event_value(raw: Any, overrides: Mapping[str, UtilityEvent]) -> UtilityEvent:
    if isinstance(raw, UtilityEvent):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("event must be one of the supported feedback labels")
    key = raw.strip().casefold().replace("-", "_").replace(" ", "_")
    try:
        return overrides[key]
    except KeyError as exc:
        supported = ", ".join(sorted(event.value for event in UtilityEvent))
        raise ValueError(f"unsupported event {raw!r}; expected one of {supported}") from exc


def _deterministic_id(parts: tuple[str, str, str, float, float]) -> str:
    body = "|".join(
        (
            parts[0],
            parts[1],
            parts[2],
            f"{parts[3]:.12g}",
            f"{parts[4]:.12g}",
        )
    )
    return "obs_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


class FeedbackNormalizer:
    """Stateful normalizer that owns an observation-id idempotency set."""

    def __init__(self, *, clock: Any = None, overrides: Mapping[str, UtilityEvent | str] | None = None) -> None:
        self._clock = clock
        self._overrides = self._coerce_overrides(overrides)
        self._seen_ids: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _coerce_overrides(overrides: Mapping[str, UtilityEvent | str] | None) -> dict[str, UtilityEvent]:
        if overrides is None:
            return dict(EVENT_OVERRIDES)
        result = dict(EVENT_OVERRIDES)
        for key, value in overrides.items():
            result[str(key).strip().casefold().replace("-", "_").replace(" ", "_")] = _event_value(value, result)
        return result

    def seed_ids(self, observation_ids: Iterable[str]) -> None:
        """Seed replay protection from a persisted scope before new events."""

        with self._lock:
            self._seen_ids.update(str(item) for item in observation_ids if str(item).strip())

    def reset(self) -> None:
        with self._lock:
            self._seen_ids.clear()

    def normalize(self, payload: Any, *, now: float | None = None) -> SignalOutcome:
        """Normalize one payload without hiding rejection or unavailability."""

        if isinstance(payload, SignalOutcome):
            return payload
        try:
            if isinstance(payload, UtilityObservation):
                data: Mapping[str, Any] = payload.model_dump(mode="python")
            elif isinstance(payload, Mapping):
                data = payload
            else:
                return self._outcome(SignalStatus.REJECTED, reason="feedback_payload_must_be_mapping_or_observation")
        except (TypeError, ValueError) as exc:
            return self._outcome(SignalStatus.REJECTED, reason=f"invalid_feedback_payload:{exc}")

        record_id = _field(data, "record_id", "memory_id", "record")
        if not isinstance(record_id, str) or not record_id.strip():
            return self._outcome(SignalStatus.REJECTED, reason="record_id_missing_or_invalid")
        record_id = record_id.strip()

        source = _field(data, "source", "feedback_source", "origin")
        if source is not None and not isinstance(source, str):
            return self._outcome(SignalStatus.REJECTED, reason="feedback_source_invalid")
        source_text = (source or "").strip()
        if not source_text or source_text.casefold() in {"unknown", "unspecified", "none"}:
            return self._outcome(
                SignalStatus.UNAVAILABLE,
                reason="feedback_source_missing",
                disclosure="unavailable: feedback source was not supplied; no observation was synthesized",
            )

        raw_event = _field(data, "event", "event_type", "feedback_type", "type", "action")
        try:
            event = _event_value(raw_event, self._overrides)
        except ValueError as exc:
            return self._outcome(SignalStatus.REJECTED, reason=str(exc))

        raw_weight = _field(data, "weight")
        if raw_weight is None:
            raw_weight = 1.0
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError, OverflowError):
            return self._outcome(SignalStatus.REJECTED, reason="weight_invalid")
        if isinstance(raw_weight, bool) or not math.isfinite(weight) or weight <= 0.0 or weight > 1000.0:
            return self._outcome(SignalStatus.REJECTED, reason="weight_out_of_range")

        raw_timestamp = _field(data, "observed_at", "timestamp", "clock_timestamp", "time")
        if raw_timestamp is None and now is not None:
            try:
                observed_at = _timestamp(now)
            except ValueError as exc:
                return self._outcome(SignalStatus.UNAVAILABLE, reason=f"clock_invalid:{exc}")
        elif raw_timestamp is not None:
            try:
                observed_at = _timestamp(raw_timestamp)
            except ValueError as exc:
                return self._outcome(SignalStatus.REJECTED, reason=f"observed_at_invalid:{exc}")
        else:
            try:
                observed_at = _clock_value(self._clock)
            except LookupError as exc:
                return self._outcome(
                    SignalStatus.UNAVAILABLE,
                    reason=str(exc),
                    disclosure="unavailable: no source timestamp and no injected clock; no observation was synthesized",
                )
            except ValueError as exc:
                return self._outcome(SignalStatus.UNAVAILABLE, reason=f"clock_invalid:{exc}")

        metadata = _field(data, "metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            return self._outcome(SignalStatus.REJECTED, reason="metadata_must_be_mapping")

        raw_id = _field(data, "observation_id", "id", "event_id", "feedback_id")
        if raw_id is None:
            observation_id = _deterministic_id((record_id, event.value, source_text, weight, observed_at))
        else:
            if not isinstance(raw_id, str) or not raw_id.strip():
                return self._outcome(SignalStatus.REJECTED, reason="observation_id_invalid")
            observation_id = raw_id.strip()

        try:
            observation = UtilityObservation(
                observation_id=observation_id,
                record_id=record_id,
                event=event,
                weight=weight,
                source=source_text,
                observed_at=observed_at,
                metadata=dict(metadata),
            )
        except (TypeError, ValueError) as exc:
            return self._outcome(SignalStatus.REJECTED, reason=f"observation_invalid:{exc}")

        with self._lock:
            if observation.observation_id in self._seen_ids:
                return SignalOutcome(
                    status=SignalStatus.DUPLICATE,
                    observation=observation,
                    reason="duplicate_observation_id",
                    disclosure="duplicate: observation id was already accepted; score was not changed",
                    observation_id=observation.observation_id,
                )
            self._seen_ids.add(observation.observation_id)
        return SignalOutcome(
            status=SignalStatus.ACCEPTED,
            observation=observation,
            reason="accepted",
            disclosure="accepted: normalized feedback observation",
            observation_id=observation.observation_id,
        )

    def normalize_many(self, payloads: Iterable[Any], *, now: float | None = None) -> list[SignalOutcome]:
        return [self.normalize(payload, now=now) for payload in payloads]

    def _outcome(
        self,
        status: SignalStatus,
        *,
        reason: str,
        disclosure: str = "",
    ) -> SignalOutcome:
        return SignalOutcome(status=status, reason=reason, disclosure=disclosure or reason)


def normalize_signal(
    payload: Any,
    *,
    clock: Any = None,
    now: float | None = None,
    overrides: Mapping[str, UtilityEvent | str] | None = None,
    seen_ids: set[str] | None = None,
) -> SignalOutcome:
    """One-shot normalization seam for hosts that do not need a state object."""

    normalizer = FeedbackNormalizer(clock=clock, overrides=overrides)
    if seen_ids:
        normalizer.seed_ids(seen_ids)
    result = normalizer.normalize(payload, now=now)
    if seen_ids is not None and result.observation_id:
        seen_ids.add(result.observation_id)
    return result


def normalize_observation(
    payload: Any,
    *,
    clock: Any = None,
    now: float | None = None,
    overrides: Mapping[str, UtilityEvent | str] | None = None,
    seen_ids: set[str] | None = None,
) -> SignalOutcome:
    """Explicitly named alias returning the same honest outcome envelope."""

    return normalize_signal(payload, clock=clock, now=now, overrides=overrides, seen_ids=seen_ids)


def normalize_feedback(
    payloads: Iterable[Any],
    *,
    clock: Any = None,
    now: float | None = None,
    overrides: Mapping[str, UtilityEvent | str] | None = None,
) -> list[SignalOutcome]:
    normalizer = FeedbackNormalizer(clock=clock, overrides=overrides)
    return normalizer.normalize_many(payloads, now=now)


normalize = normalize_signal


__all__ = [
    "EVENT_OVERRIDES",
    "OVERRIDE_MAP",
    "FeedbackNormalizer",
    "normalize_feedback",
    "normalize_observation",
    "normalize",
    "normalize_signal",
]
