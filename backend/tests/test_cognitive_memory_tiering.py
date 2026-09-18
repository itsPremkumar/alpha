"""Tests for the Cognitive Memory Consolidation and Tiered Storage Engine."""

from __future__ import annotations

import json
import time

import pytest

from agent_workspace.memory.cognitive_memory_tiering import (
    BM25_B,
    CognitiveMemoryConsolidator,
    CognitiveMemorySystem,
    ConsolidationReport,
    EpisodeOutcome,
    EpisodicMemory,
    HybridRetriever,
    MemoryItem,
    MemoryTier,
    SemanticMemory,
    WorkingMemory,
    cosine_similarity,
    relevance_decay,
    tokenize,
)
from agent_workspace.tools.builtins.cognitive_memory_tiering_tool import (
    consolidate_cognitive_memory,
    recall_agent_memory,
)


def _item(content: str, tier: MemoryTier = MemoryTier.EPISODIC, **kwargs) -> MemoryItem:
    """Build a memory item for retrieval tests."""
    return MemoryItem(
        memory_id=kwargs.pop("memory_id", f"m-{abs(hash(content)) % 100000}"),
        tier=tier,
        content=content,
        created_at=kwargs.pop("created_at", time.time()),
        importance=kwargs.pop("importance", 0.5),
    )


# ---------------------------------------------------------------------------
# Tokenization and similarity primitives
# ---------------------------------------------------------------------------


def test_tokenize_strips_stopwords_and_case_folds():
    tokens = tokenize("The PaymentService refunds money")
    assert "the" not in tokens
    assert "paymentservice" in tokens
    assert "refunds" in tokens


def test_relevance_decay_halves_at_half_life():
    assert relevance_decay(100.0, 100.0) == pytest.approx(0.5)


def test_relevance_decay_is_one_at_zero_age():
    assert relevance_decay(0.0) == pytest.approx(1.0)


def test_cosine_similarity_of_identical_texts_is_one():
    vector = _item("payment refund").tokens
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)


def test_cosine_similarity_of_disjoint_texts_is_zero():
    assert cosine_similarity(_item("alpha").tokens, _item("beta").tokens) == 0.0


# ---------------------------------------------------------------------------
# Working memory
# ---------------------------------------------------------------------------


def test_working_memory_stores_and_returns_items():
    memory = WorkingMemory()
    memory.put("step one: read the failing test")
    items = memory.items()
    assert len(items) == 1
    assert items[0].tier is MemoryTier.WORKING


def test_working_memory_respects_capacity():
    memory = WorkingMemory(capacity=3)
    for index in range(10):
        memory.put(f"step {index}")
    assert len(memory.items()) == 3
    assert memory.items()[0].content == "step 7"


def test_working_memory_expires_stale_items():
    memory = WorkingMemory(capacity=10, ttl_sec=10.0)
    now = time.time()
    memory._items.append(_item("old", MemoryTier.WORKING, created_at=now - 100.0))
    memory.put("fresh")
    assert len(memory.items()) == 1
    assert memory.items()[0].content == "fresh"


def test_working_memory_clear_returns_count():
    memory = WorkingMemory()
    memory.put("a")
    memory.put("b")
    assert memory.clear() == 2
    assert len(memory) == 0


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------


def test_episodic_memory_is_append_only():
    memory = EpisodicMemory()
    memory.record("first trajectory", task="t1")
    memory.record("second trajectory", task="t2")
    episodes = memory.episodes()
    assert [item.content for item in episodes] == ["first trajectory", "second trajectory"]


def test_episodic_memory_stores_tool_calls_and_outcome():
    memory = EpisodicMemory()
    episode = memory.record(
        "patched billing",
        task="fix billing",
        tool_calls=[{"tool": "read_file"}, {"tool": "edit_file"}],
        outcome=EpisodeOutcome.SUCCESS.value,
    )
    assert episode.metadata["task"] == "fix billing"
    assert episode.metadata["outcome"] == "success"
    assert len(episode.metadata["tool_calls"]) == 2


def test_episodic_memory_filters_by_time():
    memory = EpisodicMemory()
    now = time.time()
    memory.record("past", timestamp=now - 1000)
    memory.record("recent", timestamp=now)
    assert [item.content for item in memory.episodes(since=now - 10)] == ["recent"]


def test_episodic_memory_bounds_retention():
    memory = EpisodicMemory(max_episodes=2)
    for index in range(5):
        memory.record(f"trajectory {index}")
    assert len(memory) == 2
    assert memory.episodes()[-1].content == "trajectory 4"


# ---------------------------------------------------------------------------
# Semantic memory
# ---------------------------------------------------------------------------


def test_semantic_memory_upsert_creates_rule():
    memory = SemanticMemory()
    rule = memory.upsert("Tool X is reliable", "tool_reliability", 0.9, 3)
    assert rule.support == 3
    assert rule.confidence == pytest.approx(0.9)
    assert len(memory) == 1


def test_semantic_memory_reinforcement_accumulates_support():
    memory = SemanticMemory()
    memory.upsert("Tool X is reliable", "tool_reliability", 0.5, 2)
    second = memory.upsert("Tool X is reliable", "tool_reliability", 0.5, 3)
    assert second.support == 5
    assert second.confidence > 0.5


def test_semantic_memory_confidence_is_capped_at_one():
    memory = SemanticMemory()
    for _ in range(25):
        rule = memory.upsert("Always true", "invariant", 1.0, 1)
    assert rule.confidence <= 1.0


def test_semantic_memory_rules_are_ranked_by_confidence():
    memory = SemanticMemory()
    memory.upsert("weak rule", "x", 0.2, 1)
    memory.upsert("strong rule", "x", 0.9, 1)
    assert memory.rules()[0].statement == "strong rule"


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def test_retriever_ranks_relevant_item_first():
    retriever = HybridRetriever()
    corpus = [
        _item("unrelated database migration notes", memory_id="1"),
        _item("payment refund workflow for billing", memory_id="2"),
    ]
    hits = retriever.rank("payment refund", corpus, limit=2)
    assert hits[0]["memory_id"] == "2"


def test_retriever_applies_decay_to_stale_items():
    retriever = HybridRetriever(half_life_sec=60.0)
    now = time.time()
    corpus = [
        _item("payment refund workflow", memory_id="old", created_at=now - 600),
        _item("payment refund workflow", memory_id="new", created_at=now),
    ]
    hits = retriever.rank("payment refund", corpus, limit=2, now=now)
    assert hits[0]["memory_id"] == "new"


def test_retriever_respects_limit():
    retriever = HybridRetriever()
    corpus = [_item(f"payment note {index}", memory_id=str(index)) for index in range(10)]
    assert len(retriever.rank("payment", corpus, limit=3)) == 3


def test_retriever_honours_min_score():
    retriever = HybridRetriever()
    corpus = [_item("completely unrelated content", memory_id="1")]
    assert retriever.rank("payment refund", corpus, min_score=0.5) == []


def test_retriever_returns_empty_corpus():
    assert HybridRetriever().rank("anything", []) == []


def test_bm25_scores_relevant_documents_higher():
    retriever = HybridRetriever()
    corpus = [_item("payment payment payment", memory_id="a"), _item("payment", memory_id="b")]
    scores = retriever.bm25(tokenize("payment"), corpus)
    assert scores[0] > scores[1]
    assert BM25_B == 0.75


# ---------------------------------------------------------------------------
# Consolidation daemon
# ---------------------------------------------------------------------------


def test_consolidator_triggers_on_context_threshold():
    episodic = EpisodicMemory()
    semantic = SemanticMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, semantic, episodic_threshold=2)
    for index in range(3):
        episodic.record(
            f"trajectory {index}",
            tool_calls=[{"tool": "read_file"}],
            outcome=EpisodeOutcome.SUCCESS.value,
        )
    decision, reason = consolidator.should_consolidate()
    assert decision is True
    assert "context threshold" in reason


def test_consolidator_ignores_below_threshold():
    episodic = EpisodicMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, SemanticMemory(), episodic_threshold=10)
    episodic.record("one", tool_calls=[{"tool": "read_file"}])
    assert consolidator.should_consolidate()[0] is False


def test_consolidator_triggers_on_idle_cycle():
    episodic = EpisodicMemory()
    consolidator = CognitiveMemoryConsolidator(
        episodic, SemanticMemory(), episodic_threshold=100, idle_threshold_sec=1.0
    )
    episodic.record("one", tool_calls=[{"tool": "read_file"}])
    consolidator._last_activity_at = time.time() - 10.0
    decision, reason = consolidator.should_consolidate()
    assert decision is True
    assert "idle cycle" in reason


def test_consolidator_force_overrides_thresholds():
    episodic = EpisodicMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, SemanticMemory(), episodic_threshold=100)
    assert consolidator.should_consolidate(force=True) == (True, "forced")


def test_consolidation_distills_tool_reliability_rules():
    episodic = EpisodicMemory()
    semantic = SemanticMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, semantic, episodic_threshold=2)
    for _ in range(4):
        episodic.record(
            "edit cycle",
            tool_calls=[{"tool": "edit_file"}],
            outcome=EpisodeOutcome.SUCCESS.value,
        )
    report = consolidator.consolidate(force=True)
    assert report.triggered is True
    assert report.rules_created >= 1
    assert any("edit_file" in rule for rule in report.rules)
    assert any(rule.category == "tool_reliability" for rule in semantic.rules())


def test_consolidation_distills_failure_modes():
    episodic = EpisodicMemory()
    semantic = SemanticMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, semantic, episodic_threshold=2)
    for _ in range(3):
        episodic.record(
            "shell attempt",
            tool_calls=[{"tool": "shell"}],
            outcome=EpisodeOutcome.FAILURE.value,
        )
    consolidator.consolidate(force=True)
    categories = {rule.category for rule in semantic.rules()}
    assert "failure_mode" in categories


def test_consolidation_distills_trajectory_patterns():
    episodic = EpisodicMemory()
    semantic = SemanticMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, semantic, episodic_threshold=2)
    for _ in range(3):
        episodic.record(
            "read then edit",
            tool_calls=[{"tool": "read_file"}, {"tool": "edit_file"}],
            outcome=EpisodeOutcome.SUCCESS.value,
        )
    consolidator.consolidate(force=True)
    assert any(rule.category == "trajectory_pattern" for rule in semantic.rules())


def test_consolidation_is_idempotent_without_new_episodes():
    episodic = EpisodicMemory()
    semantic = SemanticMemory()
    consolidator = CognitiveMemoryConsolidator(episodic, semantic, episodic_threshold=1)
    for _ in range(3):
        episodic.record("x", tool_calls=[{"tool": "read_file"}], outcome="success")
    consolidator.consolidate(force=True)
    second = consolidator.consolidate()
    assert second.triggered is False
    assert isinstance(second, ConsolidationReport)


def test_consolidation_respects_minimum_support():
    episodic = EpisodicMemory()
    semantic = SemanticMemory()
    consolidator = CognitiveMemoryConsolidator(
        episodic, semantic, episodic_threshold=1, min_support=5
    )
    episodic.record("single", tool_calls=[{"tool": "rare_tool"}], outcome="success")
    consolidator.consolidate(force=True)
    assert len(semantic) == 0


# ---------------------------------------------------------------------------
# System facade
# ---------------------------------------------------------------------------


def test_system_recalls_across_all_tiers():
    system = CognitiveMemorySystem()
    system.remember_working("next step: run the payment tests")
    system.remember_episode("refactored the payment module", task="payment refactor")
    system.remember_rule("Payment refunds require idempotency keys", "invariant")
    result = system.recall("payment", limit=5)
    assert result["count"] >= 2
    tiers = {hit["tier"] for hit in result["results"]}
    assert len(tiers) >= 2


def test_system_recall_can_target_single_tier():
    system = CognitiveMemorySystem()
    system.remember_working("working note about payments")
    system.remember_episode("episodic note about payments")
    result = system.recall("payments", tiers=["working"], limit=5)
    assert all(hit["tier"] == "working" for hit in result["results"])


def test_system_status_reports_tier_occupancy():
    system = CognitiveMemorySystem()
    system.remember_working("a")
    system.remember_episode("b")
    system.remember_rule("c")
    status = system.status()
    assert status["working_items"] == 1
    assert status["episodic_items"] == 1
    assert status["semantic_rules"] == 1


def test_system_consolidate_returns_report():
    system = CognitiveMemorySystem(episodic_threshold=2)
    for _ in range(3):
        system.remember_episode(
            "cycle", tool_calls=[{"tool": "edit_file"}], outcome=EpisodeOutcome.SUCCESS.value
        )
    report = system.consolidate()
    assert report.triggered is True
    assert report.elapsed_sec >= 0.0


def test_system_persistence_roundtrip(tmp_path):
    system = CognitiveMemorySystem()
    system.remember_episode("persisted trajectory", task="t")
    system.remember_rule("persisted rule")
    path = system.save(tmp_path / "memory.json")

    restored = CognitiveMemorySystem()
    count = restored.load(path)
    assert count == 2
    assert len(restored.episodic) == 1
    assert len(restored.semantic) == 1


def test_system_load_missing_file_is_noop(tmp_path):
    assert CognitiveMemorySystem().load(tmp_path / "absent.json") == 0


def test_system_respects_episodic_threshold():
    system = CognitiveMemorySystem(episodic_threshold=50)
    system.remember_episode("only one")
    assert system.consolidate().triggered is False


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_remember_and_recall_roundtrip():
    consolidate_cognitive_memory.invoke(
        {
            "action": "remember_episode",
            "content": "refactored the billing reconciliation job",
            "task": "billing refactor",
            "tool_calls_json": json.dumps([{"tool": "edit_file"}]),
            "outcome": "success",
        }
    )
    result = recall_agent_memory.invoke({"query": "billing reconciliation", "limit": 3})
    assert result["success"] is True
    assert result["count"] >= 1
    assert "billing" in result["results"][0]["content"].lower()


def test_tool_remember_working():
    outcome = consolidate_cognitive_memory.invoke(
        {"action": "remember_working", "content": "step 4: update migrations"}
    )
    assert outcome["success"] is True
    assert outcome["memory"]["tier"] == "working"


def test_tool_remember_rule():
    outcome = consolidate_cognitive_memory.invoke(
        {"action": "remember_rule", "statement": "Always run migrations before deploy", "confidence": 0.9}
    )
    assert outcome["success"] is True
    assert outcome["rule"]["category"] == "manual"


def test_tool_consolidate_force():
    for _ in range(3):
        consolidate_cognitive_memory.invoke(
            {
                "action": "remember_episode",
                "content": "cycle",
                "tool_calls_json": json.dumps([{"tool": "edit_file"}]),
                "outcome": "success",
            }
        )
    report = consolidate_cognitive_memory.invoke({"action": "force_consolidate"})
    assert report["success"] is True
    assert report["triggered"] is True
    assert report["rules_created"] >= 1


def test_tool_status():
    outcome = consolidate_cognitive_memory.invoke({"action": "status"})
    assert outcome["success"] is True
    assert "working_items" in outcome["status"]


def test_tool_clear_tier():
    consolidate_cognitive_memory.invoke({"action": "remember_working", "content": "temp"})
    outcome = consolidate_cognitive_memory.invoke({"action": "clear", "content": "working"})
    assert outcome["success"] is True
    assert outcome["cleared"] >= 1


def test_tool_save_and_load(tmp_path):
    consolidate_cognitive_memory.invoke({"action": "remember_rule", "statement": "snapshot rule"})
    saved = consolidate_cognitive_memory.invoke(
        {"action": "save", "path": str(tmp_path / "memory.json")}
    )
    assert saved["success"] is True
    loaded = consolidate_cognitive_memory.invoke(
        {"action": "load", "path": str(tmp_path / "memory.json")}
    )
    assert loaded["success"] is True
    assert loaded["restored"] >= 1


def test_tool_save_requires_path():
    outcome = consolidate_cognitive_memory.invoke({"action": "save"})
    assert outcome["success"] is False


def test_tool_unknown_action():
    outcome = consolidate_cognitive_memory.invoke({"action": "explode"})
    assert outcome["success"] is False


def test_tool_recall_tier_filter():
    consolidate_cognitive_memory.invoke(
        {"action": "remember_rule", "statement": "deploys require a canary phase"}
    )
    result = recall_agent_memory.invoke({"query": "canary deploy", "tiers": "semantic", "limit": 5})
    assert result["success"] is True
    assert all(hit["tier"] == "semantic" for hit in result["results"])
