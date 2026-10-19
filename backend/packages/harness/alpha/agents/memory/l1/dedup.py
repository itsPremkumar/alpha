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

Observation channel (added for the central capture seam, no decision path):
``apply_decisions`` can report the ids the store CONFIRMED, so a caller can ask
"which records did this turn actually commit?" without re-deriving anything
(re-deriving would re-index records this module just merged away). It is an
opt-in out-parameter: the ``{"stored", "skipped", "updated", "merged"}`` counts
return value is unchanged, and a caller that does not pass a sink pays nothing
— no extra read, no extra allocation, and the confirmation is skipped entirely
when nothing was written. The channel observes; it never influences a decision.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .extractor import message_text
from .models import (
    MAX_HISTORY_ENTRIES,
    DedupDecision,
    DedupOutcome,
    ExtractedMemory,
    MemoryHistoryEntry,
    MemoryRecord,
    build_history_entry,
)
from .parser import parse_dedup_response
from .prompts import format_batch_conflict_prompt, get_conflict_system_prompt
from .store import L1RecordStore, tokenize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conflict-candidate expansion (Hy-Memory 2.0 adoption, slice #2)
# ---------------------------------------------------------------------------

#: Extra records pulled past ``top_k`` when ``memory.l1.conflict_expansion``
#: is on (decision: bounded, never a full scan).
_EXPANSION_EXTRA = 5
#: Hard cap on the extras that reach the prompt after the shape prefilter.
_MAX_EXTRA_CONFLICT_CANDIDATES = 5
#: Generic connectivity words dropped by the shape check — a token only
#: counts toward "same subject" if it carries subject content.
_CONFLICT_DROP_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "my",
        "me",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "they",
        "their",
        "there",
        "here",
        "from",
        "with",
        "by",
        "as",
        "has",
        "have",
        "had",
        "now",
        "new",
    }
)


def _conflict_shaped(new_content: str, candidate_content: str) -> bool:
    """Deterministic "same subject, different value" prefilter.

    Returns True only when the two texts share at least two non-stopword
    tokens AND each side carries at least one token the other lacks — the
    shape of an implicit invalidation ("review cadence moved to Wednesdays"
    vs "review cadence runs on Mondays"). It is deliberately over-inclusive:
    near-misses get through so the LLM adjudicator, not the prefilter, decides
    what a conflict is. Thin inputs (single informative token) never qualify,
    because two shared tokens is the smallest evidence of a shared SUBJECT.
    """

    new_tokens = frozenset(token for token in tokenize(new_content or "") if token not in _CONFLICT_DROP_STOPWORDS)
    cand_tokens = frozenset(token for token in tokenize(candidate_content or "") if token not in _CONFLICT_DROP_STOPWORDS)
    if len(new_tokens) < 2 or len(cand_tokens) < 2:
        return False
    if not (new_tokens - cand_tokens) or not (cand_tokens - new_tokens):
        return False
    return len(new_tokens & cand_tokens) >= 2


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
        conflict_expansion: bool = False,
        signal_boost: bool = False,
    ) -> None:
        self._store = store
        self._model = model
        self._model_name = model_name
        self._top_k = max(1, int(top_k))
        self._user_id = user_id
        self._agent_name = agent_name
        self._conflict_expansion = bool(conflict_expansion)
        self._signal_boost = bool(signal_boost)

    def _build_matches(self, candidates: list[MemoryRecord]) -> list[dict[str, Any]]:
        """Recall existing candidates per new memory (unified pool in prompt).

        With ``conflict_expansion`` on, the recall run covers
        ``top_k + _EXPANSION_EXTRA`` records and the prompt additionally keeps
        up to ``_MAX_EXTRA_CONFLICT_CANDIDATES`` lower-ranked candidates that
        pass :func:`_conflict_shaped` — the over-inclusive widening that gives
        the LLM adjudicator a chance to see an implicit invalidation that
        ranked below the ``top_k`` cut.
        """
        recall_k = self._top_k + (_EXPANSION_EXTRA if self._conflict_expansion else 0)
        matches: list[dict[str, Any]] = []
        recall = self._store.candidates_for_dedup(
            [{"record_id": r.id, "content": r.content} for r in candidates],
            recall_k,
            user_id=self._user_id,
            agent_name=self._agent_name,
            signal_boost=self._signal_boost,
        )
        for record in candidates:
            existing = recall.get(record.id, [])
            if self._conflict_expansion and len(existing) > self._top_k:
                extra_pool = existing[self._top_k :]
                extra_pool = [c for c in extra_pool if _conflict_shaped(record.content, c.content)][:_MAX_EXTRA_CONFLICT_CANDIDATES]
                existing = existing[: self._top_k] + extra_pool
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
            response = model.invoke(f"{system_prompt}\n\n{user_prompt}", config=invoke_config)
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


def _confirm_committed_ids(
    store: L1RecordStore,
    records: list[MemoryRecord],
    *,
    user_id: str | None,
    agent_name: str | None,
) -> list[str]:
    """Ids the STORE holds for ``records`` after the write, in commit order.

    Each id is confirmed individually through ``store.get`` rather than by
    re-reading the scope, for two reasons:

    - **Cost.** A second ``list_records`` would double the read cost of every
      write turn and would run against the whole scope. One lookup per
      *written* record is proportional to what this run actually wrote, and
      the caller reaches this function only when there was something to write.
    - **Honesty.** ``put_records``' return value is not a commit receipt: it
      counts ids that were *newly added*, so a replace-in-place (an id already
      present in the scope) reads as a short write even though the record is
      committed. Asking the store per id is the only primitive here that
      separates "committed" from "intended".

    Never raises. A record that cannot be confirmed is reported as absent
    rather than guessed at, because the channel must not be able to fail a
    capture turn. Ids are de-duplicated in first-commit order; consumers that
    need a set should compare as a set.
    """
    committed: list[str] = []
    seen: set[str] = set()
    for record in records:
        if record.id in seen:
            continue
        try:
            landed = store.get(record.id, user_id=user_id, agent_name=agent_name) is not None
        except Exception:  # noqa: BLE001 - an observation channel never fails a run
            logger.warning(
                "L1 dedup: could not confirm record %s; reporting it as absent",
                record.id,
                exc_info=True,
            )
            landed = False
        if landed:
            seen.add(record.id)
            committed.append(record.id)
        else:
            logger.warning(
                "L1 dedup: record %s is not in the store after the write; reporting it as absent",
                record.id,
            )
    return committed


def _inherited_history(target_records: list[MemoryRecord], *, recorded_at: float) -> list[MemoryHistoryEntry]:
    """Bounded, de-duplicated chain of replaced values for one merged record.

    Order is oldest-first: each target's inherited chain (it survived earlier
    supersessions) comes before that target's OWN current value, which is the
    freshest prior. A ``(record_id, content)`` pair is only ever embedded once,
    so a merge across targets that share ancestry cannot duplicate an entry.
    The chain is capped by dropping the OLDEST entries (they are the least
    informative about the current value), never the newest.
    """
    entries: list[MemoryHistoryEntry] = []
    seen: set[tuple[str, str]] = set()

    def _add(entry: MemoryHistoryEntry) -> None:
        key = (entry.record_id, entry.content)
        if key in seen:
            return
        seen.add(key)
        entries.append(entry)

    for target in target_records:
        for inherited in target.history:
            _add(inherited)
        _add(build_history_entry(target, recorded_at=recorded_at))
    return entries[-MAX_HISTORY_ENTRIES:]


def apply_decisions(
    store: L1RecordStore,
    candidates: list[MemoryRecord],
    outcome: DedupOutcome,
    *,
    user_id: str | None = None,
    agent_name: str | None = None,
    now: float | None = None,
    persisted_ids: list[str] | None = None,
    history_enabled: bool = False,
    kept_history: list[int] | None = None,
    superseded_ids: list[str] | None = None,
) -> dict[str, int]:
    """Apply conflict decisions to the store; returns per-action counts.

    - ``store`` (also the default when no decision matches a candidate):
      insert the candidate as a new record.
    - ``skip``: the candidate is dropped as a duplicate (counted only).
    - ``update`` / ``merge``: remove ``target_ids`` from the store and
      insert ONE merged record carrying ``merged_content`` / ``merged_type``
      / ``merged_priority`` / ``merged_timestamps`` (timestamps union falls
      back to target + candidate timestamps when the model omits them).

    Returns ``{"stored": n, "skipped": n, "updated": n, "merged": n}`` — that
    contract is unchanged for every existing caller, which keeps passing
    nothing at all.

    ``persisted_ids`` is the OPTIONAL observation channel: pass a list and it
    is cleared, then filled with the record ids the store confirmed after the
    write (see :func:`_confirm_committed_ids`). It answers "which records did
    this turn actually commit?" without re-deriving anything. Leave it ``None``
    and this function costs exactly what it did before — the confirmation only
    runs when a sink is supplied *and* something was written.

    ``kept_history`` is the second optional out-parameter (it mirrors
    ``persisted_ids`` semantics: one append per update/merge write naming the
    surviving record's history-chain length). ``history_enabled`` turns the
    Hy-Memory-2.0-style history retention ON: prior values of ``update``/
    ``merge`` targets are persisted on the surviving record instead of being
    destroyed (see :func:`_inherited_history`). Both default to OFF so every
    existing caller keeps byte-identical behaviour; history never changes a
    decision, only what survives around the decision.

    ``superseded_ids`` is the third optional out-parameter (Hy-Memory adoption
    slice #1): pass a list and it is cleared, then filled with the target ids
    the store CONFIRMED removed (verified post-delete via ``store.get``), in
    sorted order. This is the staleness-propagation observation channel — the
    pipeline walks the mention lineage of exactly these ids. Leave it ``None``
    and there is no confirmation cost.

    Per-outcome id table (the only ids ``persisted_ids`` can ever receive)::

        outcome                     reported ids
        --------------------------  --------------------------------------------
        store                       the candidate's own id.
        skip                        none — a skipped candidate is not a record
                                    that exists; reporting it would hand the
                                    capture seam a phantom memory.
        update                      the surviving id, exactly once. It is the
                                    CANDIDATE's id (the merged record's id is
                                    pinned to it), not the target's.
        merge (cross-type or not)   the single survivor id, exactly once.
        stale/unknown target_ids    none of the missing ids; the candidate
                                    degrades to ``store`` and is reported
                                    under its own id.
        partial or failed write     only the ids the store confirmed. A
                                    ``put_records`` failure still propagates
                                    unchanged, but the out-parameter already
                                    holds the partial truth.

    Merge rule, stated once so the capture seam does not have to guess: the
    SURVIVOR is the candidate's id (``merged.id`` is pinned to it, so this run's
    provenance and the stored record agree). Every ``target_id`` is ABSORBED:
    it is removed from the store and is never reported, so downstream capture
    cannot index a record that no longer exists. A target id that is itself
    written in the same run is never deleted (the replacement guard) and is
    reported once, like any other committed id.
    """
    counts = {"stored": 0, "skipped": 0, "updated": 0, "merged": 0}
    if persisted_ids is not None:
        # Cleared on every exit path (including the empty-candidate one) so a
        # caller reusing one list across runs can never read a stale id.
        persisted_ids.clear()
    if kept_history is not None:
        kept_history.clear()
    if superseded_ids is not None:
        superseded_ids.clear()
    if not candidates:
        return counts

    decisions = outcome.by_record_id()
    existing = {record.id: record for record in store.list_records(user_id, agent_name)}
    to_write: list[MemoryRecord] = []
    to_delete: set[str] = set()

    for candidate in candidates:
        decision = decisions.get(candidate.id) or DedupDecision(record_id=candidate.id, action="store")
        if decision.action == "skip":
            counts["skipped"] += 1
            continue
        if decision.action in ("update", "merge"):
            target_records = [existing[tid] for tid in decision.target_ids if tid in existing]
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
            merged_priority = decision.merged_priority if decision.merged_priority is not None else max(priorities)
            merged = MemoryRecord.create(
                decision.merged_content or candidate.content,
                memory_type=(decision.merged_type if decision.merged_type else (target_records[0].type if target_records else candidate.type)),
                priority=merged_priority,
                scene_name=candidate.scene_name or (target_records[0].scene_name if target_records else ""),
                timestamps=merged_timestamps,
                metadata={
                    **candidate.metadata,
                    "source": "l1_dedup_merge",
                    "merged_from": sorted({candidate.id, *(r.id for r in target_records)}),
                },
                now=now,
            )
            if merged.id != candidate.id:
                # Keep the merged record id stable under the candidate's id
                # so provenance for this run points at the stored record.
                merged.id = candidate.id
            if history_enabled:
                recorded_at = time.time() if now is None else now
                merged.history = _inherited_history(target_records, recorded_at=recorded_at)
                if kept_history is not None:
                    kept_history.append(len(merged.history))
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
        store.delete_records(sorted(deletable), user_id=user_id, agent_name=agent_name)
        if superseded_ids is not None:
            # The propagation channel reports only what the STORE confirmed:
            # a delete that did not land (failed call, mid-scope write) is not
            # a supersession, and claiming one would mark a live record stale.
            for target_id in sorted(deletable):
                try:
                    gone = store.get(target_id, user_id=user_id, agent_name=agent_name) is None
                except Exception:  # noqa: BLE001 - an observation channel never fails a run
                    logger.warning("L1 dedup: could not confirm removal of %s; not reporting it as superseded", target_id, exc_info=True)
                    gone = False
                if gone:
                    superseded_ids.append(target_id)
    if to_write:
        try:
            store.put_records(to_write, user_id=user_id, agent_name=agent_name)
        finally:
            # ``finally``, not ``else``: when the write raises part-way the sink
            # must still carry what the store really holds. The exception
            # continues to the caller unchanged — swallowing it here would be a
            # capture behaviour change, and reporting the write intent instead
            # of the partial reality would be a lie.
            if persisted_ids is not None:
                persisted_ids.extend(_confirm_committed_ids(store, to_write, user_id=user_id, agent_name=agent_name))
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


__all__ = ["L1Dedup", "apply_decisions", "candidate_records", "_conflict_shaped"]
