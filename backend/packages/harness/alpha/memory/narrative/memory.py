"""Injectable facade for narrative capture, regeneration, and recall.

The facade consumes plain mappings at its boundary so an episode engine or L1
pipeline can emit events without importing narrative models.  It owns no
process-wide singleton; the host chooses the config, store, and model seam.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from .config import NarrativeConfig
from .models import IngestResult, IngestStatus, NarrativeEvent, RegenerationResult, StoryDocument
from .paths import scope_key
from .provenance import append_ingest, append_regeneration, read_entries
from .recall import NarrativeRecall
from .store import NarrativeStore
from .synthesis import regenerate_story
from .timeline import NarrativeTimeline


class NarrativeMemory:
    """Composition root for one explicitly configured narrative subsystem."""

    def __init__(
        self,
        config: NarrativeConfig | None = None,
        store: NarrativeStore | None = None,
        model: Any = None,
    ) -> None:
        if store is not None and config is None:
            config = store.config
        self.config = config if config is not None else NarrativeConfig()
        self.store = store if store is not None else NarrativeStore(config=self.config)
        self.model = model
        self.timeline_view = NarrativeTimeline(self.store, self.config)
        self.recall_view = NarrativeRecall(self.store, self.config)

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @staticmethod
    def _scope_parts(scope: Any, scope_id: str | None) -> tuple[str, str]:
        key = scope_key(scope, scope_id)
        if ":" in key:
            return key.split(":", 1)
        return key, "default"

    @staticmethod
    def _mapping_event(
        value: Mapping[str, Any],
        *,
        target_scope: str,
        target_scope_id: str,
        now: float,
    ) -> NarrativeEvent:
        """Map common episode/L1 record fields without importing either owner."""

        data = dict(value)
        data.setdefault("scope", target_scope)
        if not str(data.get("scope_id") or "").strip():
            data["scope_id"] = target_scope_id
        period_start = data.get("period_start")
        timestamps = data.get("timestamps")
        if period_start is None and isinstance(timestamps, (list, tuple)) and timestamps:
            period_start = timestamps[0]
        if period_start is None:
            period_start = data.get("start_time", data.get("timestamp", data.get("created_at", now)))
        data["period_start"] = period_start
        period_end = data.get("period_end")
        if period_end is None and isinstance(timestamps, (list, tuple)) and timestamps:
            period_end = timestamps[-1]
        if period_end is None:
            period_end = data.get("end_time", period_start)
        data["period_end"] = period_end
        title = data.get("title") or data.get("action") or data.get("name") or data.get("content")
        if not title:
            title = data.get("summary") or data.get("observation") or "Untitled event"
        data["title"] = title
        summary = data.get("summary") or data.get("observation") or data.get("content") or data.get("description")
        data["summary"] = summary or title
        importance = data.get("importance", data.get("priority", data.get("salience", 50.0)))
        if "salience" in data and "importance" not in data and "priority" not in data:
            try:
                salience = float(importance)
                if salience <= 1.0:
                    importance = salience * 100.0
            except (TypeError, ValueError):
                pass
        try:
            if float(importance) < 0.0:
                importance = 100.0
        except (TypeError, ValueError):
            pass
        data["importance"] = importance
        if "participants" not in data:
            data["participants"] = data.get("people") or data.get("actors") or []
        if "outcomes" not in data:
            metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
            outcome = data.get("outcome", data.get("result", metadata.get("outcomes")))
            data["outcomes"] = outcome if isinstance(outcome, list) else ([outcome] if outcome else [])
        if "source_refs" not in data:
            refs = data.get("record_refs") or data.get("source_refs") or []
            if isinstance(refs, str):
                refs = [refs]
            elif not isinstance(refs, list):
                refs = list(refs)
            for key in ("id", "record_id", "trace_id", "message_id"):
                if data.get(key) and data[key] not in refs:
                    refs.append(data[key])
            data["source_refs"] = refs
        return NarrativeEvent.model_validate(data)

    def ingest(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]] | NarrativeEvent | Mapping[str, Any],
        scope: Any = "user",
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        now: float | None = None,
    ) -> IngestResult:
        """Consume one or more plain event dictionaries into one scope."""

        if isinstance(events, (NarrativeEvent, Mapping)):
            materialized_input = [events]
        else:
            materialized_input = list(events)
        moment = time.time() if now is None else float(now)
        effective_scope_id = scope_id or user_id or project_id or agent_id
        try:
            target_scope, target_scope_id = self._scope_parts(scope, effective_scope_id)
        except ValueError as exc:
            result = IngestResult(
                status=IngestStatus.FAILED,
                considered=len(materialized_input),
                error=str(exc)[:500],
            )
            append_ingest(
                scope=scope,
                scope_id=scope_id,
                event_count=len(materialized_input),
                stored_count=0,
                status=result.status.value,
                storage_path=str(self.store.root),
                now=moment,
                error=result.error,
            )
            return result
        if not self.enabled:
            result = IngestResult(
                status=IngestStatus.DISABLED,
                considered=len(materialized_input),
                error="narrative_memory_disabled",
            )
            append_ingest(
                scope=target_scope,
                scope_id=target_scope_id,
                event_count=len(materialized_input),
                stored_count=0,
                status=result.status.value,
                storage_path=str(self.store.root),
                now=moment,
                error=result.error,
            )
            return result
        converted: list[NarrativeEvent] = []
        errors: list[str] = []
        for value in materialized_input:
            try:
                if isinstance(value, NarrativeEvent):
                    event = value.model_copy(deep=True)
                    if event.scope_id == "default":
                        event = event.model_copy(update={"scope_id": target_scope_id}, deep=True)
                else:
                    event = self._mapping_event(
                        value,
                        target_scope=target_scope,
                        target_scope_id=target_scope_id,
                        now=moment,
                    )
                converted.append(event)
            except Exception as exc:  # noqa: BLE001 - one bad source row is disclosed
                errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        if not converted:
            result = IngestResult(
                status=IngestStatus.FAILED if materialized_input else IngestStatus.EMPTY,
                considered=len(materialized_input),
                error="; ".join(errors)[:500] or "no_events",
            )
            append_ingest(
                scope=target_scope,
                scope_id=target_scope_id,
                event_count=len(materialized_input),
                stored_count=0,
                status=result.status.value,
                storage_path=str(self.store.root),
                now=moment,
                error=result.error,
            )
            return result
        try:
            write = self.store.append_events(converted, target_scope, scope_id=target_scope_id)
        except Exception as exc:  # noqa: BLE001 - preserve source data and disclose store failure
            result = IngestResult(
                status=IngestStatus.STORE_ERROR,
                considered=len(materialized_input),
                event_ids=[event.id for event in converted],
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
            )
        else:
            status = IngestStatus.PARTIAL if errors else IngestStatus.STORED
            result = IngestResult(
                status=status,
                considered=len(materialized_input),
                stored=write.stored,
                updated=write.updated,
                event_ids=list(write.event_ids),
                evicted_event_ids=list(write.evicted_event_ids),
                error="; ".join(errors)[:500],
            )
        append_ingest(
            scope=target_scope,
            scope_id=target_scope_id,
            event_count=len(materialized_input),
            stored_count=result.stored,
            status=result.status.value,
            storage_path=str(self.store.root),
            now=moment,
            event_ids=result.event_ids,
            evicted_event_ids=result.evicted_event_ids,
            error=result.error,
        )
        return result

    def ingest_events(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]] | NarrativeEvent | Mapping[str, Any],
        scope: Any = "user",
        **kwargs: Any,
    ) -> IngestResult:
        """Alias for :meth:`ingest` used by source adapters."""

        return self.ingest(events, scope, **kwargs)

    def ingest_records(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]] | NarrativeEvent | Mapping[str, Any],
        scope: Any = "user",
        **kwargs: Any,
    ) -> IngestResult:
        """Source-seam alias for consuming stored record dictionaries."""

        return self.ingest(events, scope, **kwargs)

    def ingest_event(
        self,
        event: NarrativeEvent | Mapping[str, Any],
        scope: Any = "user",
        **kwargs: Any,
    ) -> IngestResult:
        """Ingest one source event."""

        return self.ingest(event, scope, **kwargs)

    def add_event(
        self,
        event: NarrativeEvent | Mapping[str, Any],
        scope: Any = "user",
        **kwargs: Any,
    ) -> IngestResult:
        """Alias for :meth:`ingest_event`."""

        return self.ingest_event(event, scope, **kwargs)

    def regenerate(
        self,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        model: Any = None,
        now: float | None = None,
    ) -> RegenerationResult:
        """Regenerate explicitly and record the honest outcome in provenance."""

        moment = time.time() if now is None else float(now)
        effective_scope_id = scope_id or user_id or project_id or agent_id
        selected_model = self.model if model is None else model
        try:
            result = regenerate_story(
                self.store,
                scope,
                scope_id=effective_scope_id,
                config=self.config,
                model=selected_model,
                now=moment,
                record_provenance=False,
            )
        except Exception as exc:  # noqa: BLE001 - the public boundary must disclose all failures
            old = self.store.get_story(scope, scope_id=effective_scope_id)
            result = RegenerationResult(
                status="failed",
                synthesis_status="llm_error",
                document=old,
                event_count=self.store.count(scope, scope_id=effective_scope_id),
                chars=old.total_chars if old is not None else 0,
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
                story_unchanged=True,
            )
        append_regeneration(
            scope=scope,
            scope_id=effective_scope_id,
            event_count=result.event_count,
            chapters_changed=result.changed_chapters,
            chars=result.chars,
            status=result.status,
            synthesis_status=result.synthesis_status,
            synthesis=result.document.synthesis if result.document is not None else "",
            storage_path=str(self.store.root),
            now=moment,
            error=result.error,
        )
        return result

    def regenerate_story(self, scope: Any = "user", **kwargs: Any) -> RegenerationResult:
        """Alias for :meth:`regenerate`."""

        return self.regenerate(scope, **kwargs)

    def timeline(
        self,
        scope: Any = "user",
        start: float | None = None,
        end: float | None = None,
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ):
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.timeline_view.timeline(scope, start, end, scope_id=effective_scope_id)

    def moments(
        self,
        scope: Any = "user",
        top_k: int = 5,
        query: str | None = None,
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ):
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.timeline_view.moments(scope, top_k, query, scope_id=effective_scope_id)

    def periods(
        self,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ):
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.timeline_view.periods(scope, scope_id=effective_scope_id)

    def range_summary(
        self,
        scope: Any = "user",
        start: float | None = None,
        end: float | None = None,
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ):
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.timeline_view.range_summary(scope, start, end, scope_id=effective_scope_id)

    def story_block(
        self,
        scope: Any = "user",
        limit: int = 8,
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.recall_view.story_block(scope, limit, scope_id=effective_scope_id)

    def story_path(
        self,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ):
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.recall_view.story_path(scope, scope_id=effective_scope_id)

    def get_story(
        self,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> StoryDocument | None:
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return self.store.get_story(scope, scope_id=effective_scope_id)

    def provenance(
        self,
        day: str,
        *,
        scope: Any = "user",
        scope_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_scope_id = scope_id or user_id or project_id or agent_id
        return read_entries(day, scope=scope, scope_id=effective_scope_id, storage_path=str(self.store.root))


NarrativeSubsystem = NarrativeMemory
NarrativeStoryMemory = NarrativeMemory

__all__ = ["NarrativeMemory", "NarrativeStoryMemory", "NarrativeSubsystem"]
