"""Hermetic contract tests for Alpha memory retrieval fusion/composition."""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from alpha.memory.fusion import (
    BUDGET_PROFILES,
    DEFAULT_FUSION_WEIGHTS,
    Candidate,
    ExactStage,
    FusionConfig,
    GraphBatch,
    GraphStage,
    ProceduralStage,
    SemanticStage,
    StageResult,
    TaskContext,
    TemporalStage,
    allocate_type_budgets,
    calculate_weighted_score,
    compose_context,
    deterministic_token_estimate,
    fuse_stage_results,
    fusion_enabled,
    query_hash,
    read_recall_provenance,
    run_retrieval_stages,
    select_diverse,
    select_mode,
    write_recall_provenance,
)


def candidate(
    candidate_id: str,
    *,
    memory_type: str = "project_facts",
    scope_id: str | None = "scope-a",
    content: str = "A useful project fact",
    **values: Any,
) -> Candidate:
    return Candidate(id=candidate_id, memory_type=memory_type, content=content, scope_id=scope_id, **values)


def task_context(**values: Any) -> TaskContext:
    settings: dict[str, Any] = {
        "scope_id": "scope-a",
        "task_type": "coding",
        "budget": 1000,
        "max_candidates": 20,
    }
    settings.update(values)
    return TaskContext(**settings)


# ---------------------------------------------------------------------------
# Configuration, lazy exports, and query-mode selection
# ---------------------------------------------------------------------------


def test_config_is_default_off_and_every_key_has_a_reader() -> None:
    config = FusionConfig()
    assert config.enabled is False
    assert fusion_enabled(config) is False
    assert FusionConfig(enabled=True).enabled is True
    assert config.weights == DEFAULT_FUSION_WEIGHTS
    assert math.isclose(sum(config.weights.values()), 1.0)
    assert FusionConfig(weights={"semantic": 1.0}).weights["lexical"] == 0.0
    with pytest.raises(ValueError, match="sum to 1.0"):
        FusionConfig(weights={"semantic": 0.8})
    with pytest.raises(ValueError, match="unknown fusion weights"):
        FusionConfig(weights={"invented": 1.0})

    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "fusion"
    expected_readers = {
        "enabled": "config.py",
        "weights": "fusion.py",
        "strategy": "fusion.py",
        "mmr_lambda": "composer.py",
        "max_candidates_per_stage": "fusion.py",
        "max_results": "fusion.py",
        "total_budget_tokens": "composer.py",
        "profile": "composer.py",
        "drop_contradictions": "composer.py",
        "latency_budget_ms": "fusion.py",
        "storage_path": "provenance.py",
    }
    assert set(FusionConfig.model_fields) == set(expected_readers)
    for key, filename in expected_readers.items():
        assert key in (package / filename).read_text(encoding="utf-8"), key

    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("memory_config")
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("memory_config") for alias in node.names)


def test_package_root_exports_are_lazy() -> None:
    code = (
        "import sys\n"
        "import alpha.memory.fusion as fusion\n"
        "assert fusion.FusionConfig.__name__ == 'FusionConfig'\n"
        "assert 'alpha.memory.fusion.config' in sys.modules\n"
        "assert 'alpha.memory.fusion.models' not in sys.modules\n"
        "assert 'alpha.memory.fusion.stages' not in sys.modules\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("query", "mode"),
    [
        ("open src/alpha/memory/fusion.py", "exact"),
        ("show MemorySearchError details", "exact"),
        ("what does DATABASE_URL control?", "exact"),
        ("what did the user prefer for concise answers?", "semantic"),
        ("what depends on the authentication service?", "graph"),
        ("what is the latest validated setting?", "temporal"),
        ("how did we solve this before?", "procedural"),
    ],
)
def test_mode_selection_discloses_query_reason(query: str, mode: str) -> None:
    selection = select_mode(query)
    assert selection.mode == mode
    assert selection.reason
    assert selection.matched_signals


def test_explicit_mode_wins_and_mixed_cues_select_hybrid() -> None:
    assert select_mode("latest dependency", "exact").mode == "exact"
    mixed = select_mode("what changed in the workflow that fixed the latest dependency?")
    assert mixed.mode == "hybrid"
    assert mixed.reason == "multiple_mode_signals"
    with pytest.raises(ValueError, match="unknown retrieval mode"):
        select_mode("anything", "unknown")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Stage happy paths and honest degradation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage_class", "mode", "query"),
    [
        (ExactStage, "exact", "find MemorySearchError"),
        (SemanticStage, "semantic", "find a concept"),
        (TemporalStage, "temporal", "find recent state"),
        (ProceduralStage, "procedural", "find a workflow"),
    ],
)
def test_single_provider_stages_happy_path(stage_class: type, mode: str, query: str) -> None:
    calls: list[tuple[str, str, int, TaskContext]] = []

    def provider(actual_query: str, actual_mode: str, budget: int, ctx: TaskContext) -> list[Candidate]:
        calls.append((actual_query, actual_mode, budget, ctx))
        return [candidate("stage-hit", score_components={mode: 0.9})]

    ctx = task_context()
    result = stage_class(provider).retrieve(query, mode, 321, ctx)
    assert result.status == "ok"
    assert [item.id for item in result.candidates] == ["stage-hit"]
    assert result.stage == stage_class.name
    assert result.latency_ms >= 0.0
    assert result.reason and "provider_returned=1" in result.reason
    assert calls == [(query, mode, 321, ctx)]


@pytest.mark.parametrize(
    ("stage_class", "reason"),
    [
        (ExactStage, "exact_provider_unavailable"),
        (SemanticStage, "semantic_provider_unavailable"),
        (GraphStage, "graph_provider_unavailable"),
        (TemporalStage, "temporal_provider_unavailable"),
        (ProceduralStage, "procedural_provider_unavailable"),
    ],
)
def test_stage_without_provider_is_unavailable_not_empty(stage_class: type, reason: str) -> None:
    result = stage_class(None).retrieve("anything useful", "hybrid", 100, task_context())
    assert result.status == "unavailable"
    assert result.reason == reason
    assert result.candidates == ()


def test_provider_exception_and_invalid_rows_are_disclosed() -> None:
    def broken(*_args: Any) -> list[Candidate]:
        raise RuntimeError("backend offline")

    failed = SemanticStage(broken).retrieve("concept", "semantic", 100, task_context())
    assert failed.status == "unavailable"
    assert failed.reason and failed.reason.startswith("semantic_provider_failed:RuntimeError")

    partial = ExactStage(lambda *_args: [{"not": "a candidate"}, candidate("valid")]).retrieve(
        "MemoryError",
        "exact",
        100,
        task_context(),
    )
    assert partial.status == "ok"
    assert [item.id for item in partial.candidates] == ["valid"]
    assert partial.reason and "discarded_invalid=1" in partial.reason

    no_valid = ExactStage(lambda *_args: [{"not": "a candidate"}]).retrieve(
        "MemoryError",
        "exact",
        100,
        task_context(),
    )
    assert no_valid.status == "unavailable"
    assert no_valid.reason == "exact_provider_returned_no_valid_candidates"


def test_graph_stage_multi_hop_is_depth_capped_and_cycle_safe() -> None:
    batch = GraphBatch(
        seed_ids=("a",),
        adjacency={"a": ("b", "c"), "b": ("a", "c"), "c": ("a", "b", "d")},
        candidates={node: candidate(node, memory_type="codebase_facts") for node in "abcd"},
    )
    ctx = task_context(graph_max_depth=1, graph_max_nodes=10)
    result = GraphStage(lambda *_args: batch).retrieve("dependencies", "graph", 100, ctx)
    assert result.status == "ok"
    assert [item.id for item in result.candidates] == ["a", "b", "c"]
    assert result.reason and "visited=3" in result.reason and "max_depth=1" in result.reason

    capped = GraphStage(lambda *_args: batch).retrieve(
        "dependencies",
        "graph",
        100,
        task_context(graph_max_depth=3, graph_max_nodes=2, max_candidates=1),
    )
    assert capped.status == "ok"
    assert [item.id for item in capped.candidates] == ["a"]
    assert capped.reason and "max_nodes=2" in capped.reason and "discarded_over_stage_cap=1" in capped.reason


def test_temporal_stage_passes_operation_and_provider_empty_is_empty() -> None:
    seen: list[TaskContext] = []

    def provider(_query: str, _mode: str, _budget: int, ctx: TaskContext) -> list[Candidate]:
        seen.append(ctx)
        return [candidate("changed", event_time=150.0, score_components={"temporal": 0.8})]

    ctx = task_context(
        temporal_operation="changed_in_window",
        window_start=100.0,
        window_end=200.0,
        as_of=175.0,
    )
    result = TemporalStage(provider).retrieve("what changed", "temporal", 100, ctx)
    assert result.status == "ok"
    assert seen == [ctx]
    empty = TemporalStage(lambda *_args: []).retrieve("what changed", "temporal", 100, ctx)
    assert empty.status == "empty"
    assert empty.reason == "temporal_provider_returned_no_candidates"


# ---------------------------------------------------------------------------
# Fusion scoring and ordering
# ---------------------------------------------------------------------------


def test_weighted_score_matches_hand_computed_formula_and_penalties() -> None:
    item = candidate(
        "formula",
        score_components={
            "semantic": 0.8,
            "lexical": 0.6,
            "graph": 0.4,
            "temporal": 0.2,
            "importance": 1.0,
            "confidence": 0.5,
            "task_relevance": 0.7,
            "access_history": 0.3,
            "redundancy_penalty": 0.1,
            "contradiction_penalty": 0.05,
        },
    )
    expected = 0.30 * 0.8 + 0.20 * 0.6 + 0.15 * 0.4 + 0.10 * 0.2 + 0.10 * 1.0 + 0.05 * 1.0 + 0.05 * 0.5 + 0.03 * 0.7 + 0.02 * 0.3 - 0.1 - 0.05
    assert round(expected, 12) == 0.492
    assert round(calculate_weighted_score(item, task_context=task_context()), 12) == round(expected, 12)

    result = fuse_stage_results(
        [StageResult(stage="semantic", status="ok", candidates=(item,))],
        FusionConfig(),
        task_context=task_context(),
    )
    fused = result.candidates[0]
    assert round(fused.fused_score, 12) == round(expected, 12)
    assert fused.weighted_contributions["semantic"] == pytest.approx(0.24)
    assert fused.penalty_contributions == {"redundancy_penalty": -0.1, "contradiction_penalty": -0.05}
    assert fused.stage_contributions["metadata"] == pytest.approx(0.1 + 0.05 + 0.025 + 0.006)


def test_missing_weighted_components_are_zero_and_disclosed() -> None:
    item = candidate("sparse", score_components={"semantic": 1.0})
    result = fuse_stage_results(
        [StageResult(stage="semantic", status="ok", candidates=(item,))],
        task_context=task_context(),
    )
    fused = result.candidates[0]
    assert fused.fused_score == pytest.approx(0.4)
    assert set(fused.missing_components) == {
        "lexical",
        "graph",
        "temporal",
        "importance",
        "confidence",
        "task_relevance",
        "access_history",
    }
    assert all(fused.weighted_contributions[name] == 0.0 for name in fused.missing_components)

    unscoped = candidate("unscoped", scope_id=None, score_components={"semantic": 1.0, "scope_match": 1.0})
    unscoped_result = fuse_stage_results(
        [StageResult(stage="semantic", status="ok", candidates=(unscoped,))],
        task_context=task_context(),
    )
    assert unscoped_result.candidates[0].fused_score == pytest.approx(0.3)
    assert "scope_match" in unscoped_result.candidates[0].missing_components


def test_weighted_and_rrf_have_documented_different_ordering() -> None:
    a = candidate("a", score_components={"semantic": 0.9, "lexical": 0.1})
    b = candidate("b", score_components={"semantic": 0.4, "lexical": 1.0})
    decoy = candidate("decoy", score_components={})
    exact = StageResult(stage="exact", status="ok", candidates=(b, a))
    semantic = StageResult(stage="semantic", status="ok", candidates=(decoy, a))
    weighted = fuse_stage_results(
        [exact, semantic],
        FusionConfig(strategy="weighted", max_results=2),
        task_context=task_context(),
    )
    rrf = fuse_stage_results(
        [exact, semantic],
        FusionConfig(strategy="rrf", max_results=2),
        task_context=task_context(),
    )
    assert [item.id for item in weighted.candidates] == ["b", "a"]
    assert [item.id for item in rrf.candidates] == ["a", "b"]
    assert rrf.candidates[0].stage_contributions == {"exact": pytest.approx(1 / 61), "semantic": pytest.approx(1 / 61)}


def test_fusion_tie_break_is_score_desc_then_id() -> None:
    b = candidate("b", score_components={"semantic": 0.5})
    a = candidate("a", score_components={"semantic": 0.5})
    result = fuse_stage_results(
        [StageResult(stage="semantic", status="ok", candidates=(b, a))],
        task_context=task_context(),
    )
    assert [item.id for item in result.candidates] == ["a", "b"]


def test_unavailable_stage_is_excluded_and_candidates_are_never_invented() -> None:
    returned = candidate("only-returned")
    result = fuse_stage_results(
        [
            StageResult(stage="exact", status="unavailable", reason="exact backend offline"),
            StageResult(stage="semantic", status="ok", candidates=(returned,)),
        ],
        task_context=task_context(),
    )
    assert [item.id for item in result.candidates] == ["only-returned"]
    assert result.stages_run == ("semantic",)
    assert result.stages_unavailable == {"exact": "exact backend offline"}
    assert all(item.id != "never-returned" for item in result.candidates)


def test_fusion_caps_disclose_every_drop() -> None:
    items = tuple(candidate(f"c{index}") for index in range(4))
    result = fuse_stage_results(
        [StageResult(stage="semantic", status="ok", candidates=items)],
        FusionConfig(max_candidates_per_stage=3, max_results=2, strategy="weighted"),
        task_context=task_context(),
    )
    reasons = [item.reason for item in result.dropped_with_reason]
    assert "max_candidates_per_stage" in reasons
    assert "max_results" in reasons


# ---------------------------------------------------------------------------
# Diversity and contradiction handling
# ---------------------------------------------------------------------------


def test_mmr_suppresses_near_duplicate_and_keeps_distinct_evidence() -> None:
    high = candidate("high", content="JWT auth service uses signed cookies", score_components={"semantic": 0.9})
    duplicate = candidate("duplicate", content="JWT auth service uses signed cookies", score_components={"semantic": 0.8})
    distinct = candidate("distinct", content="Deploy waits for migration completion", score_components={"semantic": 0.7})
    result = select_diverse([duplicate, distinct, high], limit=2)
    assert {item.id for item in result.selected} == {"high", "distinct"}
    near_drop = next(item for item in result.dropped_with_reason if item.candidate_id == "duplicate")
    assert near_drop.reason == "near_duplicate"
    assert near_drop.details["kept_id"] == "high"


def test_mmr_diversity_displaces_overlapping_candidate_for_distinct_evidence() -> None:
    first = candidate("first", content="alpha beta gamma delta epsilon", score_components={"semantic": 0.90})
    overlapping = candidate("overlapping", content="alpha beta gamma delta zeta", score_components={"semantic": 0.89})
    distinct = candidate("distinct", content="omega eta theta iota kappa", score_components={"semantic": 0.88})
    result = select_diverse([overlapping, distinct, first], limit=2, mmr_lambda=0.70)
    assert [item.id for item in result.selected] == ["first", "distinct"]
    assert any(item.candidate_id == "overlapping" and item.reason == "mmr_limit" for item in result.dropped_with_reason)


def test_contradiction_keeps_authority_then_newer_and_records_loser() -> None:
    authoritative = candidate(
        "authority",
        content="Production uses the verified migration",
        authority=0.9,
        created_at=100.0,
        contradiction_group="migration",
    )
    newer_but_weaker = candidate(
        "weaker",
        content="Production may use an unverified migration",
        authority=0.4,
        created_at=200.0,
        contradiction_group="migration",
    )
    result = select_diverse([newer_but_weaker, authoritative], limit=2)
    assert [item.id for item in result.selected] == ["authority"]
    loser = next(item for item in result.dropped_with_reason if item.candidate_id == "weaker")
    assert loser.reason == "contradiction_superseded"
    assert loser.details["kept_id"] == "authority"

    same_authority = newer_but_weaker.model_copy(update={"id": "newer", "authority": 0.9})
    newer_result = select_diverse([authoritative, same_authority], limit=2)
    assert [item.id for item in newer_result.selected] == ["newer"]


# ---------------------------------------------------------------------------
# Context composition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["coding", "debugging", "planning"])
def test_profile_budget_allocation_is_exact(profile: str) -> None:
    allocation = allocate_type_budgets(1000, profile)  # type: ignore[arg-type]
    assert allocation == {memory_type: int(percent * 10) for memory_type, percent in BUDGET_PROFILES[profile].items()}
    assert sum(allocation.values()) == 1000
    remainder_allocations = allocate_type_budgets(1001, profile)  # type: ignore[arg-type]
    assert sum(remainder_allocations.values()) == 1001
    assert sum(remainder_allocations[key] - allocation[key] for key in allocation) == 1


def test_coding_profile_matches_authoritative_percentages() -> None:
    assert allocate_type_budgets(1000, "coding") == {
        "identity_soul": 100,
        "project_facts": 150,
        "recent_task_state": 200,
        "codebase_facts": 200,
        "procedures_skills": 150,
        "failures_lessons": 100,
        "evidence_provenance": 100,
    }


def test_composer_filters_scope_stale_superseded_and_merges_duplicates() -> None:
    now = 1_000.0
    items = [
        candidate("keep", content="Same durable fact", provenance_refs=("evidence-1",)),
        candidate("duplicate", content="Same durable fact", provenance_refs=("evidence-2",)),
        candidate("foreign", scope_id="scope-b"),
        candidate("unscoped", scope_id=None),
        candidate("stale", content="Old", valid_to=900.0),
        candidate("old", content="Replaced"),
        candidate("new", content="Replacement", supersedes=("old",)),
    ]
    config = FusionConfig(total_budget_tokens=1000, max_results=20)
    composed = compose_context(items, config, task_context=task_context(as_of=now))
    ids = {memory_id for block in composed.blocks for memory_id in block.candidate_ids}
    assert len(ids & {"keep", "duplicate"}) == 1
    assert "foreign" not in ids
    assert "unscoped" not in ids
    assert "stale" not in ids
    assert "old" not in ids
    dropped_reasons = {(item.candidate_id, item.reason) for item in composed.dropped_with_reason}
    assert ("foreign", "scope_filtered") in dropped_reasons
    assert ("unscoped", "scope_missing") in dropped_reasons
    assert ("stale", "stale_validity") in dropped_reasons
    assert ("old", "superseded") in dropped_reasons
    assert ("keep", "duplicate_merged") in dropped_reasons or ("duplicate", "duplicate_merged") in dropped_reasons


def test_composer_uses_exact_type_budget_and_drops_oversize_block_with_reason() -> None:
    config = FusionConfig(total_budget_tokens=100, profile="coding", max_results=10)
    items = [
        candidate("small", memory_type="project_facts", content="Short fact", evidence_group="e1"),
        candidate("huge", memory_type="project_facts", content="word " * 200, evidence_group="e2"),
    ]
    composed = compose_context(items, config, task_context=task_context(budget=100))
    assert composed.budget == 100
    assert sum(composed.per_type_budget.values()) == 100
    assert composed.per_type_budget["project_facts"] == 15
    assert composed.total_tokens == deterministic_token_estimate(composed.render())
    assert composed.total_tokens <= composed.budget
    assert "small" in composed.render()
    assert "huge" not in composed.render()
    assert any(item.candidate_id == "huge" and item.reason == "candidate_block_exceeds_type_budget" for item in composed.dropped_with_reason)
    assert composed.blocks[0].evidence_groups[0].group_id == "e1"


def test_injected_tokenizer_is_used_for_exact_accounting() -> None:
    def words(text: str) -> int:
        return len(text.split())

    config = FusionConfig(total_budget_tokens=1000, max_results=5)
    item = candidate("tokenized", content="one two three four", evidence_group="evidence")
    composed = compose_context([item], config, task_context=task_context(budget=1000), token_estimator=words)
    assert composed.total_tokens == words(composed.render())
    assert composed.total_tokens <= 1000


# ---------------------------------------------------------------------------
# Latency reporting and append-only provenance
# ---------------------------------------------------------------------------


class FixedLatencyStage:
    def __init__(self, name: str, latency_ms: float) -> None:
        self.name = name
        self.latency_ms = latency_ms

    def retrieve(self, query: str, mode: str, budget: int, ctx: TaskContext) -> StageResult:
        return StageResult(stage=self.name, status="ok", candidates=(candidate(f"{self.name}-hit"),), latency_ms=self.latency_ms)


def test_latency_budget_stops_later_stages_and_fusion_reports_overrun() -> None:
    stages = [FixedLatencyStage("exact", 8.0), FixedLatencyStage("semantic", 1.0), FixedLatencyStage("graph", 1.0)]
    results = run_retrieval_stages(
        stages,
        query="anything",
        mode="hybrid",
        budget=100,
        ctx=task_context(),
        latency_budget_ms=5.0,
    )
    assert results[0].status == "ok"
    assert results[1].status == "unavailable"
    assert results[1].reason == "latency_budget_exhausted_before_stage:semantic"
    assert results[2].status == "unavailable"

    fused = fuse_stage_results(results, FusionConfig(latency_budget_ms=5.0), task_context=task_context())
    assert fused.total_latency_ms == 8.0
    assert fused.latency_budget_exceeded is True
    assert fused.latency_disclosure == "latency_budget_exceeded"


def test_provenance_is_appended_per_recall_without_raw_query(tmp_path: Path) -> None:
    mode = select_mode("latest project decision", "semantic")
    item = candidate("prov-1", score_components={"semantic": 0.8}, provenance_refs=("source-1",))
    fusion = fuse_stage_results(
        [StageResult(stage="semantic", status="ok", candidates=(item,), latency_ms=3.0)],
        FusionConfig(latency_budget_ms=100.0),
        task_context=task_context(),
    )
    composed = compose_context(
        fusion.candidates,
        FusionConfig(storage_path=str(tmp_path), total_budget_tokens=1000),
        task_context=task_context(budget=1000),
    )
    config = FusionConfig(storage_path=str(tmp_path))
    for _index in range(2):
        written = write_recall_provenance(
            "private raw prompt",
            mode=mode,
            fusion=fusion,
            context=composed,
            config=config,
            scope_id="scope-a",
            now=1_800_000_000.0,
        )
        assert written.written is True
        assert written.path and Path(written.path).exists()

    entries = read_recall_provenance("2027-01-15", scope_id="scope-a", storage_path=str(tmp_path))
    assert len(entries) == 2
    entry = entries[-1]
    assert entry["query_hash"] == query_hash("private raw prompt")
    assert "private raw prompt" not in str(entry)
    assert entry["mode"] == "semantic"
    assert entry["stages_run"] == ["semantic"]
    assert entry["results"] == [
        {
            "id": "prov-1",
            "memory_type": "project_facts",
            "fused_score": pytest.approx(0.34),
            "source_stages": ["semantic"],
            "missing_components": [
                "lexical",
                "graph",
                "temporal",
                "importance",
                "confidence",
                "task_relevance",
                "access_history",
            ],
        }
    ]
    assert entry["tokens_spent"] == composed.total_tokens
    assert entry["dropped_items"] == []
