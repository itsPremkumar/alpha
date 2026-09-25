"""Injectable capture and recall facade for entity memory.

``EntityMemory`` owns no process-wide singleton.  Hosts inject an
:class:`EntityConfig`, store, and optional model; ingestion consumes only plain
record dictionaries so it stays decoupled from the L1 storage model.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .config import EntityConfig
from .extraction import EntityExtractor
from .models import (
    Entity,
    EntityIngestResult,
    EntityLink,
    ExtractionOutcome,
    ExtractionStatus,
    IngestStatus,
)
from .provenance import append_entity_ingest, append_entity_merge
from .recall import EntityRecall
from .store import EntityStore


class EntityMemory:
    """Compose deterministic-first extraction, linking, retention, and recall."""

    def __init__(
        self,
        config: EntityConfig | None = None,
        store: EntityStore | None = None,
        model: Any = None,
        *,
        extractor: str | None = None,
    ) -> None:
        if store is not None and config is None:
            config = store.config
        self.config = config if config is not None else EntityConfig()
        self.store = store if store is not None else EntityStore(config=self.config)
        self._model = model
        self._extractor_mode = extractor
        self._recall = EntityRecall(self.config, self.store)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _extractor(self) -> EntityExtractor:
        if self._extractor_mode not in {None, "deterministic", "deterministic+model"}:
            raise ValueError("extractor must be deterministic or deterministic+model")
        if self._extractor_mode is not None:
            mode = self._extractor_mode
        else:
            mode = "deterministic+model" if self.config.enable_model_extraction else "deterministic"
        return EntityExtractor(
            self._model,
            model_name=self.config.extraction_model,
            extractor=mode,
        )

    def extract(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        enable_model: bool | None = None,
    ) -> ExtractionOutcome:
        """Run extraction without writing; failures use the closed status set."""
        if not self.config.enabled:
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                errors=["entity_memory_disabled"],
            )
        try:
            return self._extractor().extract(
                records,
                enable_model=self.config.enable_model_extraction if enable_model is None else enable_model,
            )
        except Exception as exc:  # noqa: BLE001 - defensive extraction boundary
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                extractor="deterministic+model",
                model_status=ExtractionStatus.LLM_ERROR,
                errors=[str(exc)[:500] or type(exc).__name__],
            )

    def _record_times(self, records: list[Mapping[str, Any]]) -> dict[str, float]:
        times: dict[str, float] = {}
        for record in records:
            record_id = str(record.get("id") or record.get("record_id") or "").strip()
            if not record_id:
                continue
            value = record.get("created_at", record.get("updated_at"))
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number >= 0.0:
                times[record_id] = number
        return times

    def ingest_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        user_id: str,
        now: float | None = None,
    ) -> EntityIngestResult:
        """Consume plain stored-record dicts and index only explicit entities."""
        materialized = [record for record in records if isinstance(record, Mapping)]
        if not self.config.enabled:
            return EntityIngestResult(
                status=IngestStatus.SKIPPED,
                extraction_status=ExtractionStatus.EMPTY,
                extractor=self._configured_extractor_name(),
                records_considered=len(materialized),
                reason="entity_memory_disabled",
            )
        if not isinstance(user_id, str) or not user_id.strip():
            result = EntityIngestResult(
                status=IngestStatus.FAILED,
                extraction_status=ExtractionStatus.EMPTY,
                extractor=self._configured_extractor_name(),
                records_considered=len(materialized),
                errors=["user_id_required"],
                reason="missing_user_scope",
            )
            return result

        outcome = self.extract(materialized)
        if not outcome.candidates:
            failure = outcome.status not in {ExtractionStatus.OK, ExtractionStatus.EMPTY}
            result = EntityIngestResult(
                status=IngestStatus.FAILED if failure else IngestStatus.SKIPPED,
                extraction_status=outcome.status,
                model_status=outcome.model_status,
                extractor=outcome.extractor,
                records_considered=len(materialized),
                deterministic_candidates=outcome.deterministic_count,
                model_candidates=outcome.model_count,
                rejected_items=outcome.rejected_items,
                errors=list(outcome.errors),
                reason=outcome.status.value,
            )
            append_entity_ingest(
                result,
                user_id=user_id,
                storage_path=str(self.store.root),
                now=now,
            )
            return result

        try:
            write = self.store.ingest_candidates(
                outcome.candidates,
                user_id=user_id,
                record_times=self._record_times(materialized),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - disclose storage failure to caller
            result = EntityIngestResult(
                status=IngestStatus.FAILED,
                extraction_status=outcome.status,
                model_status=outcome.model_status,
                extractor=outcome.extractor,
                records_considered=len(materialized),
                candidates=len(outcome.candidates),
                deterministic_candidates=outcome.deterministic_count,
                model_candidates=outcome.model_count,
                rejected_items=outcome.rejected_items,
                errors=[*outcome.errors, str(exc)[:500] or type(exc).__name__],
                reason="store_error",
            )
        else:
            result = EntityIngestResult(
                status=IngestStatus.SUCCEEDED,
                extraction_status=outcome.status,
                model_status=outcome.model_status,
                extractor=outcome.extractor,
                records_considered=len(materialized),
                candidates=len(outcome.candidates),
                deterministic_candidates=outcome.deterministic_count,
                model_candidates=outcome.model_count,
                stored_mentions=write.stored_mentions,
                new_entities=write.new_entities,
                resolved_entities=write.resolved_entities,
                merged_entities=write.merged_entities,
                evicted_entities=len(write.evicted_entity_ids),
                entity_ids=list(write.entity_ids),
                evicted_entity_ids=list(write.evicted_entity_ids),
                rejected_items=outcome.rejected_items,
                errors=list(outcome.errors),
            )
        append_entity_ingest(
            result,
            user_id=user_id,
            storage_path=str(self.store.root),
            now=now,
        )
        return result

    def add_link(self, link: EntityLink, *, user_id: str) -> EntityLink | None:
        return self.store.add_link(link, user_id=user_id)

    def merge_entities(
        self,
        target_id: str,
        source_id: str,
        *,
        user_id: str,
        now: float | None = None,
    ) -> Entity | None:
        merged = self.store.merge_entities(target_id, source_id, user_id=user_id)
        if merged is not None:
            append_entity_merge(
                user_id=user_id,
                storage_path=str(self.store.root),
                target_id=target_id,
                source_id=source_id,
                merged_entity_id=merged.id,
                mention_count=merged.mention_count,
                first_seen=merged.first_seen,
                last_seen=merged.last_seen,
                now=now,
            )
        return merged

    def resolve(self, name: str, *, user_id: str, include_unpromoted: bool = False) -> Entity | None:
        return self._recall.resolve(
            name,
            user_id=user_id,
            include_unpromoted=include_unpromoted,
        )

    def entities_for_record(
        self,
        record_id: str,
        *,
        user_id: str,
        include_unpromoted: bool = False,
    ) -> list[Entity]:
        return self._recall.entities_for_record(
            record_id,
            user_id=user_id,
            include_unpromoted=include_unpromoted,
        )

    def records_for_entity(self, name: str, *, user_id: str, include_unpromoted: bool = False) -> list[str]:
        return self._recall.records_for_entity(
            name,
            user_id=user_id,
            include_unpromoted=include_unpromoted,
        )

    def neighborhood(
        self,
        entity: str | Entity,
        *,
        user_id: str,
        depth: int = 1,
        include_unpromoted: bool = False,
    ) -> list[Entity]:
        return self._recall.neighborhood(
            entity,
            user_id=user_id,
            depth=depth,
            include_unpromoted=include_unpromoted,
        )

    def render_block(
        self,
        name: str | None = None,
        *,
        user_id: str,
        max_lines: int = 8,
        max_chars: int = 2_400,
        include_unpromoted: bool = False,
    ) -> str:
        return self._recall.render_block(
            name,
            user_id=user_id,
            max_lines=max_lines,
            max_chars=max_chars,
            include_unpromoted=include_unpromoted,
        )

    def stats(self, user_id: str) -> dict[str, int | str | dict[str, int]]:
        return self._recall.stats(user_id)

    def _configured_extractor_name(self) -> str:
        if self._extractor_mode is not None:
            return self._extractor_mode
        return "deterministic+model" if self.config.enable_model_extraction else "deterministic"


__all__ = ["EntityMemory"]
