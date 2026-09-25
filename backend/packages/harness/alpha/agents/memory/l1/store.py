"""L1 record store: per-scope JSON documents + hybrid (BM25 + vector) search.

Adapted from TencentDB-Agent-Memory ``MemoryCore/src/core/record/l1-writer.ts``
(record identity, store/update/skip/merge write strategy, timestamp-trail and
versioning) and ``MemoryCore/src/core/store/bm25-local.ts`` +
``search-utils.ts`` (hybrid BM25 + similarity recall), MIT, Copyright (C) 2026
Tencent — see ``docs/THIRD_PARTY_MEMORY_NOTICES.md``.

Design decisions:

- One JSON document per ``(user_id, agent_name)`` scope is the source of
  truth. ``update`` / ``merge`` remove their target records in real time so
  retrieval never sees stale duplicates (the source removes targets from its
  vector store while its JSONL stays append-only; Alpha's document store can
  replace in place, so no background compaction pass is needed).
- Hybrid ranking blends Okapi BM25 with a token/character-ngram cosine
  similarity (weights and the BM25 constants are documented below). The same
  ``search()`` feeds dedup candidate recall AND recall injection, so the two
  phases can never disagree about relevance.
- Document writes are atomic (temp + ``os.replace``); scopes are guarded by
  per-scope re-entrant locks; a corrupt document is preserved on disk under a
  ``.corrupt`` name and the scope restarts empty with a loud warning (never
  a silent overwrite of unreadable data).
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.models import MemoryRecord
from alpha.agents.memory.l1.paths import atomic_write_text, l1_root, records_path

logger = logging.getLogger(__name__)

#: Document schema version for forward compatibility.
_SCHEMA = 1
#: Bounded per-thread processed-message cursor (ids beyond this window are
#: forgotten, which can re-extract at most one old batch — same trade-off the
#: DeerMem watermark makes, documented in AGENTS.md).
_CURSOR_LIMIT = 500
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")

# Hybrid ranking constants (documented, not configurable — one ranking
# policy for dedup recall and prompt recall):
_BM25_K1 = 1.5
_BM25_B = 0.75
_W_BM25 = 0.6
_W_COSINE = 0.4
#: BM25 scores above this are treated as saturated when normalising.
_BM25_SATURATION = 5.0


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens (length >= 2) — the shared ranking tokenizer."""
    return _TOKEN_RE.findall((text or "").lower())


def _char_ngrams(text: str, n: int = 3) -> list[str]:
    compact = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if len(compact) < n:
        return [compact] if compact else []
    return [compact[i : i + n] for i in range(len(compact) - n + 1)]


def _bm25_scores(query_tokens: list[str], doc_tokens: list[list[str]]) -> list[float]:
    """Okapi BM25 over the in-memory corpus (standard Lucene IDF)."""
    if not doc_tokens:
        return []
    n_docs = len(doc_tokens)
    doc_freq: Counter[str] = Counter()
    for tokens in doc_tokens:
        for term in set(tokens):
            doc_freq[term] += 1
    avg_len = sum(len(t) for t in doc_tokens) / n_docs or 1.0
    scores: list[float] = []
    for tokens in doc_tokens:
        tf = Counter(tokens)
        score = 0.0
        for term in query_tokens:
            if term not in tf:
                continue
            idf = math.log(1 + (n_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            freq = tf[term]
            denom = freq + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * len(tokens) / avg_len)
            score += idf * (freq * (_BM25_K1 + 1.0)) / denom
        scores.append(score)
    return scores


def _cosine_terms(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(count * b.get(term, 0) for term, count in a.items())
    norm_a = math.sqrt(sum(count * count for count in a.values()))
    norm_b = math.sqrt(sum(count * count for count in b.values()))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def hybrid_score(query: str, record: MemoryRecord, *, corpus_tokens: list[list[str]], index: int) -> float:
    """Blend normalised BM25 (over the whole scope corpus) with a token +
    character-trigram cosine similarity against this record."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    doc_tokens = corpus_tokens[index]
    bm25 = _bm25_scores(query_tokens, [doc_tokens])[0]
    norm_bm25 = min(1.0, bm25 / _BM25_SATURATION)
    token_cos = _cosine_terms(Counter(query_tokens), Counter(doc_tokens))
    gram_cos = _cosine_terms(Counter(_char_ngrams(query)), Counter(_char_ngrams(record.content)))
    semantic = 0.6 * token_cos + 0.4 * gram_cos
    return _W_BM25 * norm_bm25 + _W_COSINE * semantic


class _ScopeDocument:
    """In-memory state for one ``(user_id, agent_name)`` scope."""

    __slots__ = ("records", "processed", "last_scene", "dirty")

    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []
        #: thread_id -> bounded list of processed message keys.
        self.processed: dict[str, list[str]] = {}
        #: thread_id -> last scene name (extraction continuity cursor).
        self.last_scene: dict[str, str] = {}
        self.dirty = False


class L1RecordStore:
    """Thread-safe, per-scope L1 record store with hybrid search."""

    def __init__(self, storage_path: str | None = None):
        self._root: Path = l1_root(storage_path)
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._cache: dict[tuple[str, str], _ScopeDocument] = {}

    # -- locking ----------------------------------------------------------
    def _key_lock(self, user_id: str | None, agent_name: str | None) -> threading.RLock:
        key = (user_id or "", agent_name or "")
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def _path(self, user_id: str | None, agent_name: str | None) -> Path:
        return records_path(self._root, user_id, agent_name)

    def _load(self, user_id: str | None, agent_name: str | None) -> _ScopeDocument:
        key = (user_id or "", agent_name or "")
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        doc = _ScopeDocument()
        path = self._path(user_id, agent_name)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                doc.records = [MemoryRecord.model_validate(item) for item in raw.get("records", [])]
                doc.processed = {str(k): [str(x) for x in v] for k, v in (raw.get("processed") or {}).items() if isinstance(v, list)}
                doc.last_scene = {str(k): str(v) for k, v in (raw.get("last_scene") or {}).items() if isinstance(v, str)}
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
                # Preserve the unreadable document for forensics; never overwrite
                # it silently and never crash the turn over it.
                backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
                try:
                    path.replace(backup)
                    logger.error("L1 store: corrupt document %s preserved as %s; starting scope empty (%s)", path, backup.name, exc)
                except OSError:
                    logger.error("L1 store: corrupt document %s could not be preserved (%s)", path, exc)
                doc = _ScopeDocument()
        self._cache[key] = doc
        return doc

    def _persist(self, user_id: str | None, agent_name: str | None, doc: _ScopeDocument) -> None:
        payload = {
            "schema": _SCHEMA,
            "records": [record.model_dump() for record in doc.records],
            "processed": doc.processed,
            "last_scene": doc.last_scene,
        }
        atomic_write_text(self._path(user_id, agent_name), json.dumps(payload, ensure_ascii=False, indent=1))
        doc.dirty = False

    # -- record API -------------------------------------------------------
    def list_records(self, user_id: str | None = None, agent_name: str | None = None) -> list[MemoryRecord]:
        """All records in one scope (snapshot copy)."""
        with self._key_lock(user_id, agent_name):
            return [record.model_copy(deep=True) for record in self._load(user_id, agent_name).records]

    def count(self, user_id: str | None = None, agent_name: str | None = None) -> int:
        with self._key_lock(user_id, agent_name):
            return len(self._load(user_id, agent_name).records)

    def get(self, record_id: str, *, user_id: str | None = None, agent_name: str | None = None) -> MemoryRecord | None:
        with self._key_lock(user_id, agent_name):
            for record in self._load(user_id, agent_name).records:
                if record.id == record_id:
                    return record.model_copy(deep=True)
        return None

    def put_records(
        self,
        records: list[MemoryRecord],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        """Add or replace (by id) records in one scope. Returns count added."""
        if not records:
            return 0
        with self._key_lock(user_id, agent_name):
            doc = self._load(user_id, agent_name)
            by_id = {record.id: index for index, record in enumerate(doc.records)}
            added = 0
            for record in records:
                existing = by_id.get(record.id)
                if existing is None:
                    doc.records.append(record.model_copy(deep=True))
                    by_id[record.id] = len(doc.records) - 1
                    added += 1
                else:
                    doc.records[existing] = record.model_copy(deep=True)
            if added:
                self._persist(user_id, agent_name, doc)
            return added

    def delete_records(
        self,
        record_ids: list[str],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        """Remove records by id (the ``update``/``merge`` target removal)."""
        if not record_ids:
            return 0
        wanted = set(record_ids)
        with self._key_lock(user_id, agent_name):
            doc = self._load(user_id, agent_name)
            before = len(doc.records)
            doc.records = [record for record in doc.records if record.id not in wanted]
            removed = before - len(doc.records)
            if removed:
                self._persist(user_id, agent_name, doc)
            return removed

    # -- hybrid search ----------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        memory_type: str | None = None,
    ) -> list[MemoryRecord]:
        """Hybrid BM25 + cosine recall, highest score first.

        ``memory_type`` filters BEFORE the ``top_k`` slice so a type-scoped
        search is never starved by other types' higher scores.
        """
        with self._key_lock(user_id, agent_name):
            records = self._load(user_id, agent_name).records
        if not records or not (query or "").strip():
            return []
        if memory_type:
            records = [record for record in records if record.type == memory_type]
            if not records:
                return []
        corpus_tokens = [tokenize(record.content) for record in records]
        scored = [(hybrid_score(query, record, corpus_tokens=corpus_tokens, index=index), record) for index, record in enumerate(records)]
        scored.sort(key=lambda pair: -pair[0])
        return [record for score, record in scored[: max(0, top_k)] if score > 0.0]

    # -- dedup candidate recall ------------------------------------------
    def candidates_for_dedup(
        self,
        new_memories: list[dict[str, Any]],
        top_k: int,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, list[MemoryRecord]]:
        """Recall up to ``top_k`` existing candidates for each new memory.

        ``new_memories`` items are dicts with ``record_id`` and ``content``.
        Mirrors the source's per-memory candidate recall feeding the unified
        conflict pool.
        """
        matches: dict[str, list[MemoryRecord]] = {}
        for memory in new_memories:
            record_id = memory.get("record_id") or ""
            matches[record_id] = self.search(str(memory.get("content", "")), top_k, user_id=user_id, agent_name=agent_name)
        return matches

    # -- extraction cursors ----------------------------------------------
    def processed_keys(self, thread_id: str, *, user_id: str | None = None, agent_name: str | None = None) -> set[str]:
        with self._key_lock(user_id, agent_name):
            return set(self._load(user_id, agent_name).processed.get(thread_id, []))

    def mark_processed(
        self,
        thread_id: str,
        keys: list[str],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Append message keys to the thread cursor (bounded window)."""
        if not keys:
            return
        with self._key_lock(user_id, agent_name):
            doc = self._load(user_id, agent_name)
            merged = doc.processed.get(thread_id, [])
            merged = merged + [key for key in keys if key not in set(merged)]
            doc.processed[thread_id] = merged[-_CURSOR_LIMIT:]
            self._persist(user_id, agent_name, doc)

    def last_scene(self, thread_id: str, *, user_id: str | None = None, agent_name: str | None = None) -> str | None:
        with self._key_lock(user_id, agent_name):
            return self._load(user_id, agent_name).last_scene.get(thread_id)

    def set_last_scene(
        self,
        thread_id: str,
        scene_name: str,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        if not scene_name:
            return
        with self._key_lock(user_id, agent_name):
            doc = self._load(user_id, agent_name)
            doc.last_scene[thread_id] = scene_name
            self._persist(user_id, agent_name, doc)

    # -- lifecycle --------------------------------------------------------
    def reset(self) -> None:
        """Drop every cached scope (tests / storage_path switches)."""
        with self._locks_guard:
            self._cache.clear()
            self._locks.clear()

    @property
    def root(self) -> Path:
        return self._root


_store_singleton: L1RecordStore | None = None
_store_lock = threading.Lock()


def get_l1_store() -> L1RecordStore:
    """Process-wide L1 record store (follows ``memory.l1.storage_path``)."""
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _store_lock:
        if _store_singleton is None:
            from alpha.config.memory_config import get_memory_config

            _store_singleton = L1RecordStore(get_memory_config().l1.storage_path)
        return _store_singleton


def reset_l1_store() -> None:
    """Clear the store singleton (tests / config reload)."""
    global _store_singleton
    with _store_lock:
        _store_singleton = None


__all__ = ["L1RecordStore", "get_l1_store", "hybrid_score", "reset_l1_store", "tokenize"]
