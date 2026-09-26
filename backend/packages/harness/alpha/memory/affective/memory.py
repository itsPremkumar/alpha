"""Composable capture and recall facade for affective memory.

``AffectiveMemory`` owns no global singleton. Callers inject a config, store,
and (when explicitly enabled) model, which keeps production wiring replaceable
and tests hermetic.
"""

from __future__ import annotations

import time
from typing import Any

from .config import AffectiveConfig
from .extraction import AffectiveExtractor
from .models import (
    AffectEvent,
    AffectSource,
    AffectSubject,
    ExtractionOutcome,
    ExtractionStatus,
    IngestResult,
    IngestStatus,
    MoodState,
)
from .mood import compute_mood
from .provenance import append_event_ingest, append_mood_computation
from .recall import render_block, select_recent_moments
from .store import AffectiveEventStore


class AffectiveMemory:
    """Injectable facade for explicit labels, optional extraction, and recall."""

    def __init__(
        self,
        config: AffectiveConfig | None = None,
        store: AffectiveEventStore | None = None,
        model: Any = None,
    ) -> None:
        self.config = config if config is not None else AffectiveConfig()
        self.store = store if store is not None else AffectiveEventStore(self.config.storage_path)
        self._model = model

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _storage_path(self) -> str:
        return str(self.store.root)

    def _record_ingest(self, event: AffectEvent, result: IngestResult) -> None:
        append_event_ingest(
            event,
            result,
            storage_path=self._storage_path(),
            now=event.created_at,
        )

    def ingest_event(self, event: AffectEvent) -> IngestResult:
        """Ingest one already-labeled event without ever guessing its labels."""
        if not self.enabled:
            result = IngestResult(
                status=IngestStatus.DISABLED,
                considered=1,
                error="affective_memory_disabled",
            )
            self._record_ingest(event, result)
            return result
        if event.confidence < self.config.min_confidence:
            result = IngestResult(
                status=IngestStatus.BELOW_CONFIDENCE,
                considered=1,
                filtered=1,
                event_ids=[event.id],
                error="below_min_confidence",
            )
            self._record_ingest(event, result)
            return result

        try:
            write = self.store.append(
                [event],
                user_id=event.user_id,
                max_events=self.config.max_events_per_user,
            )
        except Exception as exc:  # noqa: BLE001 - disclose a store failure to the caller
            result = IngestResult(
                status=IngestStatus.STORE_ERROR,
                considered=1,
                event_ids=[event.id],
                error=str(exc)[:500],
            )
        else:
            result = IngestResult(
                status=IngestStatus.STORED,
                considered=1,
                stored=len(write.event_ids),
                event_ids=list(write.event_ids),
                evicted_event_ids=list(write.evicted_event_ids),
            )
        self._record_ingest(event, result)
        return result

    def ingest_explicit(
        self,
        *,
        user_id: str,
        content: str,
        subject: AffectSubject | str,
        valence: float,
        arousal: float,
        intensity: float,
        confidence: float = 1.0,
        agent_name: str | None = None,
        record_refs: list[str] | None = None,
        event_id: str | None = None,
        now: float | None = None,
    ) -> IngestResult:
        """Ingest honest caller-supplied labels; no sentiment inference occurs."""
        if not self.enabled:
            result = IngestResult(
                status=IngestStatus.DISABLED,
                considered=1,
                error="affective_memory_disabled",
            )
            return result
        values: dict[str, Any] = {
            "user_id": user_id,
            "agent_name": agent_name,
            "subject": subject,
            "content": content,
            "valence": valence,
            "arousal": arousal,
            "intensity": intensity,
            "confidence": confidence,
            "source": AffectSource.EXPLICIT,
            "record_refs": list(record_refs or []),
            "created_at": time.time() if now is None else now,
        }
        if event_id:
            values["id"] = event_id
        return self.ingest_event(AffectEvent(**values))

    def extract(
        self,
        text: str,
        *,
        user_id: str,
        agent_name: str | None = None,
        now: float | None = None,
    ) -> ExtractionOutcome:
        """Run optional model extraction, including closed honest failure states."""
        if not self.enabled:
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                error="affective_memory_disabled",
            )
        if self._model is None and not self.config.extraction_model:
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                error="no_model_configured",
            )
        if not self.config.enable_model_extraction:
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                error="model_extraction_disabled",
            )
        extractor = AffectiveExtractor(
            model=self._model,
            model_name=self.config.extraction_model,
        )
        return extractor.extract(
            text,
            user_id=user_id,
            agent_name=agent_name,
            now=now,
        )

    def ingest_extraction(self, outcome: ExtractionOutcome) -> IngestResult:
        """Persist only events from a successful extraction outcome."""
        if not outcome.ok:
            return IngestResult(
                status=IngestStatus.EXTRACTION_FAILED,
                considered=len(outcome.events),
                error=outcome.error or outcome.status.value,
            )

        stored = 0
        filtered = 0
        event_ids: list[str] = []
        evicted: list[str] = []
        for event in outcome.events:
            result = self.ingest_event(event)
            if result.status is IngestStatus.STORE_ERROR:
                return IngestResult(
                    status=IngestStatus.STORE_ERROR,
                    considered=len(outcome.events),
                    stored=stored,
                    filtered=filtered,
                    event_ids=event_ids,
                    evicted_event_ids=evicted,
                    error=result.error,
                )
            if result.status is IngestStatus.BELOW_CONFIDENCE:
                filtered += 1
            if result.status is IngestStatus.STORED:
                stored += result.stored
                event_ids.extend(result.event_ids)
                evicted.extend(result.evicted_event_ids)

        if stored and filtered:
            status = IngestStatus.PARTIAL
        elif stored:
            status = IngestStatus.STORED
        elif filtered:
            status = IngestStatus.BELOW_CONFIDENCE
        else:
            status = IngestStatus.EMPTY
        return IngestResult(
            status=status,
            considered=len(outcome.events),
            stored=stored,
            filtered=filtered,
            event_ids=event_ids,
            evicted_event_ids=evicted,
            error="below_min_confidence" if filtered else "",
        )

    def recent_moments(
        self,
        user_id: str,
        top_k: int | None = None,
        query: str | None = None,
        *,
        agent_name: str | None = None,
    ) -> list[AffectEvent]:
        """Return a bounded user-scoped emotional-moment slice."""
        if not self.enabled or self.store.read_status(user_id) != "ok":
            return []
        limit = self.config.max_surfaced if top_k is None else min(top_k, self.config.max_surfaced)
        events = self.store.list_events(user_id)
        return select_recent_moments(
            events,
            top_k=max(0, limit),
            query=query,
            min_confidence=self.config.min_confidence,
            agent_name=agent_name,
        )

    def mood_state(self, user_id: str, now: float | None = None) -> MoodState | None:
        """Compute and disclose mood from readable store data only."""
        computed_at = time.time() if now is None else now
        if not self.enabled:
            append_mood_computation(
                None,
                status="disabled",
                user_id=user_id,
                storage_path=self._storage_path(),
                event_count=0,
                now=computed_at,
            )
            return None
        status = self.store.read_status(user_id)
        if status != "ok":
            append_mood_computation(
                None,
                status=status,
                user_id=user_id,
                storage_path=self._storage_path(),
                event_count=0,
                now=computed_at,
                error="store_unavailable",
            )
            return None
        events = self.store.list_events(user_id)
        mood = compute_mood(events, now=computed_at, half_life_hours=self.config.half_life_hours)
        append_mood_computation(
            mood,
            status=mood.status.value,
            user_id=user_id,
            storage_path=self._storage_path(),
            event_count=len(events),
            now=computed_at,
        )
        return mood if mood.sample_size > 0 else None

    def render_block(
        self,
        user_id: str,
        *,
        top_k: int | None = None,
        query: str | None = None,
        agent_name: str | None = None,
        now: float | None = None,
    ) -> str:
        """Render the bounded prompt block for this user."""
        return render_block(
            self,
            user_id,
            top_k=top_k,
            query=query,
            agent_name=agent_name,
            now=now,
        )


__all__ = ["AffectiveMemory"]
