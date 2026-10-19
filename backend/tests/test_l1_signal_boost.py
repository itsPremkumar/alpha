"""Entity/recency signal boost for L1 hybrid recall (Hy-Memory adoption slice #4).

Design: Mem0's April-2026 retrieval reports a large gain from FUSING a second
entity-identity score into lexical+semantic ranking (+23.1% multi-hop). Alpha's
L1 hybrid rank is BM25 + token/char-ngram cosine, both case-folding — so the
identity signal that is invisible in the current rank is a CASE-SENSITIVE
identifier match ("AuthService" vs "authservice") plus record recency.

When ``memory.l1.signal_boost`` is on, ``L1RecordStore.search`` adds two
documented, LLM-free terms to the hybrid score:

* ``entity_term``  = fraction of the query's identifier-shaped tokens
  (CamelCase / snake_case / digit-bearing) found case-sensitively in the
  record content, weighted ``SIGNAL_WEIGHT_ENTITY = 0.15``;
* ``recency_term`` = ``1 - age_days / SIGNAL_RECENCY_WINDOW_DAYS`` clamped to
  ``[0, 1]`` (records older than 14 days contribute nothing), weighted
  ``SIGNAL_WEIGHT_RECENCY = 0.10``.

With the gate off the store keeps byte-identical behaviour, and the SAME
knob feeds both recall and dedup candidate recall, so the two phases can
never disagree about relevance while the gate is on.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from alpha.agents.memory.l1.dedup import L1Dedup
from alpha.agents.memory.l1.models import MemoryRecord
from alpha.agents.memory.l1.pipeline import L1Pipeline
from alpha.agents.memory.l1.store import (
    L1RecordStore,
    identifier_tokens,
    recency_term,
)
from alpha.config.memory_config import L1MemoryConfig, MemoryConfig


def make_config(tmp_path: Any, **l1_overrides: Any) -> MemoryConfig:
    l1 = {"enabled": True, "storage_path": str(tmp_path / "l1"), "debounce_seconds": 0.0, "persona_enabled": False}
    l1.update(l1_overrides)
    return MemoryConfig.model_validate({"enabled": True, "l1": l1})


# ---------------------------------------------------------------------------
# signal primitives
# ---------------------------------------------------------------------------


def test_signal_boost_gate_defaults_off() -> None:
    assert L1MemoryConfig().signal_boost is False
    assert MemoryConfig().l1.signal_boost is False


def test_identifier_tokens_are_case_sensitive_and_shape_based() -> None:
    tokens = identifier_tokens("Fix AuthService and internal auth_service in mcp2, nightly.")
    assert tokens == {"AuthService", "auth_service", "mcp2"}


def test_identifier_tokens_empty_for_prose() -> None:
    assert identifier_tokens("all lowercase words here") == set()


def test_recency_term_is_bounded_and_deterministic() -> None:
    now = time.time()
    record = MemoryRecord.create("Deploy on AuthService tonight.", memory_type="work_fact")
    record.updated_at = now - 2 * 86400
    fresh = recency_term(record, now=now)
    record.updated_at = now - 100 * 86400
    stale = recency_term(record, now=now)
    assert fresh == pytest.approx(1 - 2 / 14)
    assert stale == 0.0


# ---------------------------------------------------------------------------
# store ordering
# ---------------------------------------------------------------------------


def _tie_store(tmp_path: Any) -> tuple[L1RecordStore, MemoryRecord, MemoryRecord]:
    """Two records whose case-folded token content is IDENTICAL.

    Only the identifier shape and the recency differ, so the hybrid rank is a
    tie and everything after it comes from the boost. The 'lost' variant is
    seeded FIRST, so the stable equal-score order without the boost puts it on
    top and a boost-on inversion is observable.
    """
    store = L1RecordStore(str(tmp_path / "tie"))
    fresh_match = MemoryRecord.create("Deploy runs in AuthService nightly.", memory_type="work_fact")
    lost_match = MemoryRecord.create("Deploy runs in authservice nightly.", memory_type="work_fact")
    lost_match.updated_at = time.time() - 2 * 86400
    fresh_match.updated_at = time.time()
    store.put_records([lost_match, fresh_match], user_id="u1", agent_name="agent")
    return store, lost_match, fresh_match


def test_without_boost_the_tie_keeps_corpus_order(tmp_path: Any) -> None:
    store, lost_match, fresh_match = _tie_store(tmp_path)
    order = [record.id for record in store.search("AuthService daily", top_k=2, user_id="u1", agent_name="agent")]
    assert order == [lost_match.id, fresh_match.id]


def test_with_boost_the_identifier_match_wins(tmp_path: Any) -> None:
    store, lost_match, fresh_match = _tie_store(tmp_path)
    order = [record.id for record in store.search("AuthService daily", top_k=2, user_id="u1", agent_name="agent", signal_boost=True)]
    assert order[0] == fresh_match.id, "case-sensitive entity match outranks a token tie"
    assert lost_match.id in order


def test_with_boost_the_fresher_tied_record_wins(tmp_path: Any) -> None:
    store = L1RecordStore(str(tmp_path / "recency"))
    older = MemoryRecord.create("Deploy runs in auth service nightly.", memory_type="work_fact")
    fresher = MemoryRecord.create("Deploy runs in auth service nightly too.", memory_type="work_fact")
    older.updated_at = time.time() - 100 * 86400
    fresher.updated_at = time.time()
    store.put_records([older, fresher], user_id="u1", agent_name="agent")

    order = [record.id for record in store.search("auth service", top_k=2, user_id="u1", agent_name="agent", signal_boost=True)]

    assert order[0] == fresher.id, "recency breaks a token tie when the boost is on"


def test_search_without_records_or_query_is_unchanged(tmp_path: Any) -> None:
    store = L1RecordStore(str(tmp_path / "s"))
    assert store.search("q", 3, user_id="u1", agent_name="agent", signal_boost=True) == []
    assert store.search("   ", 3, user_id="u1", agent_name="agent", signal_boost=True) == []


# ---------------------------------------------------------------------------
# dedup share + pipeline wiring
# ---------------------------------------------------------------------------


def test_candidates_for_dedup_passes_signal_boost_through(tmp_path: Any) -> None:
    store, lost_match, fresh_match = _tie_store(tmp_path)
    new_record = MemoryRecord.create("AuthService daily run", memory_type="work_fact")
    plain = store.candidates_for_dedup([{"record_id": new_record.id, "content": new_record.content}], 2, user_id="u1", agent_name="agent")
    boosted = store.candidates_for_dedup(
        [{"record_id": new_record.id, "content": new_record.content}],
        2,
        user_id="u1",
        agent_name="agent",
        signal_boost=True,
    )
    assert [r.id for r in plain[new_record.id]] == [lost_match.id, fresh_match.id]
    assert [r.id for r in boosted[new_record.id]] == [fresh_match.id, lost_match.id]


def test_pipeline_recall_uses_the_configured_boost(tmp_path: Any) -> None:
    cfg = make_config(tmp_path, signal_boost=True)
    store, _lost_match, fresh_match = _tie_store(tmp_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=None)

    block = pipeline.recall(query="AuthService daily", user_id="u1", agent_name="agent")

    assert fresh_match.content in block
    block_index_fresh = block.index(fresh_match.content)
    block_index_stale = block.index("authservice")
    assert block_index_fresh < block_index_stale, "the identifier match is injected first"


def test_dedup_constructor_takes_the_boost(tmp_path: Any) -> None:
    store, _lost, _fresh = _tie_store(tmp_path)
    dedup = L1Dedup(store, None, top_k=5, user_id="u1", agent_name="agent", signal_boost=True)
    assert dedup._signal_boost is True
