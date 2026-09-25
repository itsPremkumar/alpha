"""The injected-clock facade for the memory-utility feedback loop.

``MemoryUtility`` is the only orchestration surface most hosts need.  It
normalizes feedback, updates only utility metadata, and exposes pure retention
and dedup recommendations.  It intentionally has no method for deleting or
editing a host memory record: ``apply`` persists utility records only, and the
host must authorize any destructive action independently.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .config import UtilityConfig
from .dedup import Similarity, suggest_dedup
from .models import (
    BudgetState,
    DedupSuggestion,
    ObserveResult,
    RetentionDecision,
    ScoreLabel,
    SignalStatus,
    UtilityObservation,
    UtilityRecord,
)
from .retention import retention_decisions
from .scoring import (
    CalibrationResult,
    ScoreResult,
    merge_observation,
    score_record,
)
from .scoring import (
    calibrate as calibrate_samples,
)
from .signals import FeedbackNormalizer
from .store import UtilityStore


class MemoryUtility:
    """Default-off facade for utility feedback and proposal generation."""

    def __init__(
        self,
        config: UtilityConfig | Mapping[str, Any] | None = None,
        *,
        store: UtilityStore | None = None,
        clock: Any = None,
    ) -> None:
        self.config = self._coerce_config(config)
        self._clock = clock
        self._store = store if store is not None else UtilityStore(config=self.config)
        self._normalizer = FeedbackNormalizer(clock=clock)
        self._calibration: CalibrationResult | None = None
        self.last_apply_disclosure = ""

    @staticmethod
    def _coerce_config(config: UtilityConfig | Mapping[str, Any] | None) -> UtilityConfig:
        if config is None:
            return UtilityConfig()
        if isinstance(config, UtilityConfig):
            return config
        return UtilityConfig.from_mapping(config)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def store(self) -> UtilityStore:
        return self._store

    def _now(self, now: float | None, clock: Any = None) -> float | None:
        if now is not None:
            try:
                value = float(now)
            except (TypeError, ValueError, OverflowError):
                return None
            return value if math.isfinite(value) and value >= 0.0 else None
        selected_clock = clock if clock is not None else self._clock
        if selected_clock is None:
            return None
        try:
            value = float(selected_clock() if callable(selected_clock) else selected_clock.now())
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) and value >= 0.0 else None

    def _disabled_observe(self) -> ObserveResult:
        return ObserveResult(
            status=SignalStatus.DISABLED,
            reason="utility_disabled",
            disclosure="disabled: memory utility feedback is opt-in; no observation was stored",
        )

    @staticmethod
    def _payload_with_keywords(
        payload: Any,
        *,
        event: Any = None,
        record_id: str | None = None,
        source: str | None = None,
        weight: float | None = None,
        observed_at: float | None = None,
        timestamp: float | None = None,
        clock_timestamp: float | None = None,
        observation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Any:
        if isinstance(payload, str) and event is not None and record_id is None:
            record_id = payload
            payload = None
        if payload is None:
            payload = {}
        if isinstance(payload, UtilityObservation):
            data = payload.model_dump(mode="python")
        elif isinstance(payload, Mapping):
            data = dict(payload)
        else:
            return payload
        values = {
            "event": event,
            "record_id": record_id,
            "source": source,
            "weight": weight,
            "observed_at": observed_at if observed_at is not None else timestamp if timestamp is not None else clock_timestamp,
            "observation_id": observation_id,
            "metadata": metadata,
        }
        for key, value in values.items():
            if value is not None:
                data[key] = value
        return data

    def observe(
        self,
        payload: Any = None,
        event: Any = None,
        *,
        record_id: str | None = None,
        source: str | None = None,
        weight: float | None = None,
        observed_at: float | None = None,
        timestamp: float | None = None,
        clock_timestamp: float | None = None,
        observation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
        now: float | None = None,
        clock: Any = None,
    ) -> ObserveResult:
        """Normalize and persist one utility observation.

        The returned envelope distinguishes accepted, duplicate, rejected,
        unavailable, and disabled outcomes.  No rejected event contributes to
        an aggregate.
        """

        if not self.enabled:
            return self._disabled_observe()
        normalized_payload = self._payload_with_keywords(
            payload,
            event=event,
            record_id=record_id,
            source=source,
            weight=weight,
            observed_at=observed_at,
            timestamp=timestamp,
            clock_timestamp=clock_timestamp,
            observation_id=observation_id,
            metadata=metadata,
        )
        existing_records = self._store.list_records(user_id, agent_name)
        for existing in existing_records:
            self._normalizer.seed_ids(existing.observation_ids)
        active_now = self._now(now, clock)
        outcome = self._normalizer.normalize(normalized_payload, now=active_now)
        if outcome.status is not SignalStatus.ACCEPTED:
            record = self._store.get(outcome.record_id, user_id=user_id, agent_name=agent_name) if outcome.record_id else None
            return ObserveResult(
                status=outcome.status,
                record=record,
                reason=outcome.reason,
                disclosure=outcome.disclosure,
                observation_id=outcome.observation_id,
            )
        observation = outcome.observation
        if observation is None:
            return ObserveResult(
                status=SignalStatus.UNAVAILABLE,
                reason="accepted outcome had no observation",
                disclosure="unavailable: normalizer returned no observation",
            )
        existing = self._store.get(observation.record_id, user_id=user_id, agent_name=agent_name)
        if active_now is None:
            active_now = observation.observed_at
        scored = merge_observation(
            existing,
            observation,
            now=active_now,
            config=self.config,
            calibration=self._calibration,
        )
        if scored.record is None:
            return ObserveResult(
                status=SignalStatus.UNAVAILABLE,
                reason=scored.reason or "score unavailable",
                disclosure=scored.disclosure,
                observation_id=observation.observation_id,
            )
        if self._store.put(scored.record, user_id=user_id, agent_name=agent_name) != 1:
            return ObserveResult(
                status=SignalStatus.UNAVAILABLE,
                reason="utility_store_disabled",
                disclosure="unavailable: injected utility store did not accept the record",
                observation_id=observation.observation_id,
            )
        disclosure = scored.disclosure
        evictions = self._store.evictions(user_id, agent_name)
        if evictions and evictions[-1].record_id == scored.record.record_id:
            disclosure = f"{disclosure}; record was disclosed as evicted by utility bounds"
        return ObserveResult(
            status=SignalStatus.ACCEPTED,
            record=scored.record,
            reason=scored.reason,
            disclosure=disclosure,
            observation_id=observation.observation_id,
        )

    def observe_many(
        self,
        payloads: Iterable[Any],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        now: float | None = None,
        clock: Any = None,
    ) -> list[ObserveResult]:
        """Normalize a batch with shared idempotency state."""

        if not self.enabled:
            return []
        return [self.observe(item, user_id=user_id, agent_name=agent_name, now=now, clock=clock) for item in payloads]

    def _unavailable_score(self, reason: str, sample_size: int = 0) -> ScoreResult:
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=sample_size,
            confidence=0.0,
            disclosure=f"unavailable: {reason}",
            reason=reason,
        )

    def score(
        self,
        record_id: str | UtilityRecord,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        now: float | None = None,
        clock: Any = None,
        calibration: CalibrationResult | None = None,
    ) -> ScoreResult | None:
        """Return an honest current score; disabled means no result at all."""

        if not self.enabled:
            return None
        if isinstance(record_id, UtilityRecord):
            record = record_id.model_copy(deep=True)
        else:
            record = self._store.get(str(record_id), user_id=user_id, agent_name=agent_name)
        if record is None:
            return self._unavailable_score("record has no accepted utility observations")
        active_calibration = calibration if calibration is not None else self._calibration
        active_now = self._now(now)
        if active_now is None and clock is not None:
            try:
                candidate = float(clock() if callable(clock) else clock.now())
                active_now = candidate if math.isfinite(candidate) and candidate >= 0.0 else None
            except (AttributeError, TypeError, ValueError, OverflowError):
                active_now = None
        return score_record(record, now=active_now, config=self.config, calibration=active_calibration)

    def rank(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        now: float | None = None,
        clock: Any = None,
    ) -> list[UtilityRecord]:
        """Rank current utility records by score, recency, then id."""

        if not self.enabled:
            return []
        records = self._store.list_records(user_id, agent_name)
        active_now = self._now(now)
        if active_now is None and clock is not None:
            try:
                candidate = float(clock() if callable(clock) else clock.now())
                active_now = candidate if math.isfinite(candidate) and candidate >= 0.0 else None
            except (AttributeError, TypeError, ValueError, OverflowError):
                active_now = None
        if active_now is not None:
            rescored = [score_record(item, now=active_now, config=self.config, calibration=self._calibration).record for item in records]
            records = [item for item in rescored if item is not None]
        return sorted(records, key=lambda item: (-item.score, -item.last_seen, item.record_id))

    def retention_decisions(
        self,
        *,
        budget_state: BudgetState | Mapping[str, Any] | None = None,
        budget: BudgetState | Mapping[str, Any] | None = None,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[RetentionDecision]:
        """Return proposal-only decisions for the current scope."""

        if not self.enabled:
            return []
        selected_budget = budget_state if budget_state is not None else budget
        return retention_decisions(
            self._store.list_records(user_id, agent_name),
            budget_state=selected_budget,
            config=self.config,
        )

    def dedup_suggestions(
        self,
        similarity: Similarity,
        *,
        threshold: float = 0.85,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[DedupSuggestion]:
        """Return similarity proposals without removing any record."""

        if not self.enabled:
            return []
        return suggest_dedup(
            self._store.list_records(user_id, agent_name),
            similarity=similarity,
            threshold=threshold,
        )

    @staticmethod
    def _coerce_records(value: Any) -> list[UtilityRecord]:
        if isinstance(value, UtilityRecord):
            return [value]
        if isinstance(value, Mapping):
            if "record_id" in value:
                candidates = [value]
            else:
                candidates = list(value.values())
        else:
            try:
                candidates = list(value)
            except TypeError:
                return []
        result: list[UtilityRecord] = []
        for candidate in candidates:
            if isinstance(candidate, UtilityRecord):
                result.append(candidate)
            elif isinstance(candidate, Mapping):
                try:
                    result.append(UtilityRecord.model_validate(dict(candidate)))
                except (TypeError, ValueError):
                    continue
        return result

    def apply(
        self,
        records: Any,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        """Persist utility records only; never apply retention or dedup actions."""

        if not self.enabled:
            self.last_apply_disclosure = "disabled: no utility records applied"
            return 0
        parsed = self._coerce_records(records)
        if not parsed:
            self.last_apply_disclosure = "no utility records supplied; no host action taken"
            return 0
        count = self._store.apply(parsed, user_id=user_id, agent_name=agent_name)
        self.last_apply_disclosure = f"persisted {count} utility record(s) only; no memory record was deleted or merged"
        return count

    def snapshot(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """Return serializable utility state plus the exact config read."""

        scope = {"user_id": str(user_id or "default"), "agent_name": str(agent_name or "default")}
        if not self.enabled:
            return {
                "enabled": False,
                "scope": scope,
                "records": [],
                "evictions": [],
                "corruption_events": [],
                "config": self.config.read_all_keys(),
                "disclosure": "disabled: utility feedback is a no-op",
            }
        state = self._store.snapshot(user_id, agent_name)
        state.update(
            {
                "scope": scope,
                "config": self.config.read_all_keys(),
                "calibration": self._calibration.to_dict() if self._calibration else None,
                "disclosure": "heuristic utility metadata; retention/dedup outputs are proposals only",
            }
        )
        return state

    def reset_scope(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> bool:
        """Reset only this utility scope; disabled is a no-op."""

        if not self.enabled:
            return False
        self._calibration = None
        return self._store.reset_scope(user_id=user_id, agent_name=agent_name)

    def calibrate(
        self,
        samples: Any,
        *,
        min_samples: int | None = None,
    ) -> CalibrationResult:
        """Run the explicit calibration seam and retain its honest result."""

        if not self.enabled:
            return CalibrationResult(
                calibrated=False,
                label=ScoreLabel.HEURISTIC,
                sample_size=0,
                min_samples=min_samples or self.config.min_calibration_samples,
                disclosure="calibration_refused: utility feedback is disabled; label remains heuristic",
            )
        result = calibrate_samples(samples, min_samples=min_samples, config=self.config)
        self._calibration = result
        return result


# Naming aliases keep the facade easy to discover without adding behavior.
MemoryUtilityFacade = MemoryUtility
UtilityFeedback = MemoryUtility

__all__ = ["MemoryUtility", "MemoryUtilityFacade", "UtilityFeedback"]
