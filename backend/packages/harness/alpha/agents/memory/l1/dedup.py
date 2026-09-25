"""L1 dedup engine: batch conflict detection over candidate memories.

Design provenance: the batch conflict-detection contract (per-candidate
``store`` / ``skip`` / ``update`` / ``merge`` decisions over a unified
candidate pool recalled per new memory, many-to-many ``target_ids`` merges,
timestamp-union bookkeeping) follows ``tencentdb-agent-memory``
``MemoryCore/src/core/l1-dedup.ts`` (MIT) — see
``docs/THIRD_PARTY_MEMORY_NOTICES.md``. The prompt itself lives in
:mod:`alpha.agents.memory.l1.prompts`; this module only recalls, invokes,
and applies.

Write semantics (mirroring the source's l1-writer): ``update``/``merge``
REPLACE their target records in real time (targets are removed, one merged
record lands), so retrieval never sees stale duplicates. Missing targets or
missing decisions degrade to ``store`` — content is never silently dropped.
"""

from __future__ import annotations

import logging
from typing import Any

from .extractor import message_text
from .models import DedupDecision, DedupOutcome, ExtractedMemory, MemoryRecord
from .parser import parse_dedup_response
from .prompts import format_batch_conflict_prompt, get_conflict_system_prompt
from .store import L1RecordStore

logger = logging.getLogger(__name__)


class L1Dedup:
    """Runs conflict detection for a batch of candidate memories."""

    def __init__(
        self,
        store: L1RecordStore,
        model: Any = None,
        *,
        top_k: int = 5,
        user_id: str | None = None,
        agent_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self._store = store
        self._model = model
        self._model_name = model_name
        self._top_k = max(1, int(top_k))
        self._user_id = user_id
        self._agent_name = agent_name

    def _build_matches(
        self, candidates: list[MemoryRecord]
    ) -> list[dict[str, Any]]:
        """Recall existing candidates per new memory (unified pool in prompt)."""
        matches: list[dict[str, Any]] = []
        recall = self._store.candidates_for_dedup(
            [{"record_id": r.id, "content": r.content} for r in candidates],
            self._top_k,
            user_id=self._user_id,
            agent_name=self._agent_name,
        )
        for record in candidates:
            existing = recall.get(record.id, [])
            matches.append(
                {
                    "record": record.to_prompt_dict(),
                    "candidates": [c.to_candidate_dict() for c in existing],
                }
            )
        return matches

    def detect(
        self,
        candidates: list[MemoryRecord],
        *,
        mode: str = "chat",
        thread_id: str = "",
    ) -> DedupOutcome:
        """Detect conflicts between candidate records and the store.

        Never raises: an LLM failure returns ``status="llm_error"`` and the
        caller applies the fail-open path (store everything).
        """
        if not candidates:
            return DedupOutcome(status="empty")
        model = self._model
        if model is None:
            return DedupOutcome(status="llm_error", error="no_model_configured")

        matches = self._build_matches(candidates)
        system_prompt = get_conflict_system_prompt(mode)
        user_prompt = format_batch_conflict_prompt(matches)
        invoke_config: dict[str, Any] = {
            "run_name": "l1_memory_dedup",
            "metadata": {"thread_id": thread_id, "candidate_count": len(candidates)},
        }
        try:
            response = model.invoke(
                f"{system_prompt}\n\n{user_prompt}", config=invoke_config
            )
        except BaseException as exc:  # noqa: BLE001 - fail open, never crash the turn
            logger.warning("L1 dedup LLM call failed: %s", exc)
            return DedupOutcome(status="llm_error", error=str(exc)[:500])

        outcome = parse_dedup_response(message_text(response))
        try:
            from .quota import usage_from_response

            outcome.credits = usage_from_response(response, model=self._model_name)
        except Exception:  # noqa: BLE001 - usage bookkeeping must not fail a run
            outcome.credits = 0.0
        if outcome.status != "ok":
            logger.info("L1 dedup response not usable (status=%s)", outcome.status)
        return outcome


def apply_decisions(
    store: L1RecordStore,
    candidates: list[MemoryRecord],
    outcome: DedupOutcome,
    *,
    user_id: str | None = None,
    agent_name: str | None = None,
    now: float | None = None,
) -> dict[str, int]:
    """Apply conflict decisions to the store; returns per-action counts.

    - ``store`` (also the default when no decision matches a candidate):
      insert the candidate as a new record.
    - ``skip``: the candidate is dropped as a duplicate (counted only).
    - ``update`` / ``merge``: remove ``target_ids`` from the store and
      insert ONE merged record carrying ``merged_content`` / ``merged_type``
      / ``merged_priority`` / ``merged_timestamps`` (timestamps union falls
      back to target + candidate timestamps when the model omits them).

    Returns ``{"stored": n, "skipped": n, "updated": n, "merged": n}``.
    """
    counts = {"stored": 0, "skipped": 0, "updated": 0, "merged": 0}
    if not candidates:
        return counts

    decisions = outcome.by_record_id()
    existing = {record.id: record for record in store.list_records(user_id, agent_name)}
    to_write: list[MemoryRecord] = []
    to_delete: set[str] = set()

    for candidate in candidates:
        decision = decisions.get(candidate.id) or DedupDecision(
            record_id=candidate.id, action="store"
        )
        if decision.action == "skip":
            counts["skipped"] += 1
            continue
        if decision.action in ("update", "merge"):
            target_records = [
                existing[tid] for tid in decision.target_ids if tid in existing
            ]
            # Stale/unknown targets: fall through to a plain store so no
            # content disappears (the source's writer behaves the same way).
            if not target_records:
                to_write.append(candidate)
                counts["stored"] += 1
                continue
            merged_timestamps = list(decision.merged_timestamps)
            if not merged_timestamps:
                seen: set[str] = set()
                for stamp in [
                    *candidate.timestamps,
                    *(ts for rec in target_records for ts in rec.timestamps),
                ]:
                    if stamp and stamp not in seen:
                        seen.add(stamp)
                        merged_timestamps.append(stamp)
            priorities = [candidate.priority, *[r.priority for r in target_records]]
            merged_priority = (
                decision.merged_priority
                if decision.merged_priority is not None
                else max(priorities)
            )
            merged = MemoryRecord.create(
                decision.merged_content or candidate.content,
                memory_type=(
                    decision.merged_type
                    if decision.merged_type
                    else (target_records[0].type if target_records else candidate.type)
                ),
                priority=merged_priority,
                scene_name=candidate.scene_name
                or (target_records[0].scene_name if target_records else ""),
                timestamps=merged_timestamps,
                metadata={
                    **candidate.metadata,
                    "source": "l1_dedup_merge",
                    "merged_from": sorted(
                        {candidate.id, *(r.id for r in target_records)}
                    ),
                },
                now=now,
            )
            if merged.id != candidate.id:
                # Keep the merged record id stable under the candidate's id
                # so provenance for this run points at the stored record.
                merged.id = candidate.id
            to_delete.update(decision.target_ids)
            to_write.append(merged)
            counts["updated" if decision.action == "update" else "merged"] += 1
            continue
        # store (default / no matching decision)
        to_write.append(candidate)
        counts["stored"] += 1

    # A target must never be deleted without its replacement landing.
    write_ids = {record.id for record in to_write}
    deletable = {tid for tid in to_delete if tid not in write_ids}
    if deletable:
        store.delete_records(
            sorted(deletable), user_id=user_id, agent_name=agent_name
        )
    if to_write:
        store.put_records(to_write, user_id=user_id, agent_name=agent_name)
    return counts


def candidate_records(
    memories: list[ExtractedMemory],
    *,
    scene_name: str = "",
    now: float | None = None,
    source: str = "l1_extraction",
) -> list[MemoryRecord]:
    """Turn extracted candidates into storable records (ids assigned here)."""
    records: list[MemoryRecord] = []
    for memory in memories:
        records.append(
            MemoryRecord.create(
                memory.content,
                memory_type=memory.memory_type,
                priority=memory.priority,
                scene_name=scene_name,
                metadata={
                    **memory.metadata,
                    "source": source,
                    "source_message_ids": memory.source_message_ids,
                },
                now=now,
            )
        )
    return records


__all__ = ["L1Dedup", "apply_decisions", "candidate_records"]
