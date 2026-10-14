"""Behavioural tests for the Active Memory Controller and the hardened router.

These cover the properties the active-memory design calls load-bearing rather
than mock-only happy paths: unavailable is not empty, relevance is not truth,
scope is a filter and not a score term, bounds are refused rather than clamped,
and a controller with no evidence abstains instead of fabricating a confident
answer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from alpha.memory.active_controller import (
    ActiveMemoryController,
    MemorySourceHealth,
    RetrievedMemory,
    WorkingSet,
    _DreamRuleStage,
    _evidence_state,
    _memory_type_for,
    _SessionHistoryStage,
    _significant_tokens,
)
from alpha.memory.active_memory import ActiveMemoryRouter, MemoryLookupResult
from alpha.memory.dreaming.store import DreamStore
from alpha.memory.fusion.config import FusionConfig
from alpha.memory.fusion.models import Candidate

# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


class FakeDreamStore:
    """Minimal DreamStore stand-in with an explicit, inspectable path."""

    def __init__(self, text: str, path: str = "/runtime/memory/MEMORY.md") -> None:
        self._text = text
        self.memory_path = path

    def read_memory(self) -> str:
        return self._text


class FakeManager:
    """Configured-backend double supporting the tier-2 ``search`` contract."""

    def __init__(self, rows: list[dict] | None = None, *, raises: Exception | None = None) -> None:
        self._rows = rows or []
        self._raises = raises
        self.calls: list[dict] = []

    def search(self, query: str, *, top_k: int, user_id: str, agent_name: str | None, category: str | None = None) -> list[dict]:
        self.calls.append({"query": query, "top_k": top_k, "user_id": user_id, "agent_name": agent_name})
        if self._raises is not None:
            raise self._raises
        return self._rows[:top_k]


class SearchlessManager:
    """A backend that never implemented the tier-2 search contract."""

    def get_context(self, **kwargs: object) -> str:
        return ""


class FakeSessionEngine:
    def __init__(self, rows: list | None = None, *, raises: Exception | None = None) -> None:
        self._rows = rows or []
        self._raises = raises

    def search_discovery(self, query: str, limit: int = 10, *, exclude_session_ids: list[str] | None = None) -> list:
        if self._raises is not None:
            raise self._raises
        return self._rows[:limit]


class FakeEpisode:
    def __init__(self, session_id: str, message_id: str, snippet: str, score: float, timestamp: float) -> None:
        self.session_id = session_id
        self.message_id = message_id
        self.snippet = snippet
        self.score = score
        self.timestamp = timestamp


MEMORY_MD = "# Long-Term Durable Memory\n\n- Deploy script requires AWS_REGION=us-east-1 and credentials configured.\n- Database connection pool max size is 25 connections.\n"


def _line_number(candidate: Candidate) -> int:
    """Parse the trailing ``:line`` out of a ``path:line`` source reference."""
    for ref in candidate.source_refs:
        _, _, line = ref.rpartition(":")
        if line.isdigit():
            return int(line)
    return 0


def make_controller(**kwargs) -> ActiveMemoryController:
    defaults = {
        "config": FusionConfig(enabled=True),
        "dream_store": FakeDreamStore(MEMORY_MD),
        "memory_manager": FakeManager([{"id": "f1", "content": "Deploy uses us-east-1", "category": "context", "confidence": 0.9}]),
        "session_engine": None,
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return ActiveMemoryController(**defaults)


# ---------------------------------------------------------------------------
# Phase 1: the controller queries real adapters and stays honest
# ---------------------------------------------------------------------------


def test_controller_queries_multiple_adapters_and_keeps_source_provenance():
    controller = make_controller(session_engine=FakeSessionEngine([FakeEpisode("s1", "m1", "Deploy failed on us-east-1 credentials", 0.8, 1_700_000_000.0)]))

    working_set = controller.retrieve("What AWS region does the deploy script use?")

    assert isinstance(working_set, WorkingSet)
    assert working_set.selected, "expected at least one selected memory"
    # Every selected record must name where it came from.
    assert all(item.source for item in working_set.selected)
    # Provenance must survive to the record: the dream rule carries a path:line
    # pointing at the actual MEMORY.md line that matched (line 3 of the fixture).
    dream_hits = [item for item in working_set.selected if item.source == "dream_rules"]
    assert dream_hits, "expected the curated MEMORY.md rule to match a deploy/region query"
    assert any(ref.endswith("MEMORY.md:3") for ref in dream_hits[0].source_refs)


def test_unconfigured_source_is_unavailable_not_empty():
    """A missing session engine must never read as 'no past conversations'."""
    controller = make_controller(session_engine=None)

    working_set = controller.retrieve("What AWS region does the deploy script use?")

    by_stage = {result.stage: result for result in working_set.stages}
    assert by_stage["session_history"].status == "unavailable"
    assert by_stage["session_history"].reason == "session_search_engine_not_configured"
    # The other sources still answer, and the degradation is disclosed.
    assert "session_history" in working_set.degraded_sources
    assert by_stage["dream_rules"].status == "ok"


def test_failing_backend_reports_unavailable_with_its_real_reason():
    controller = make_controller(memory_manager=FakeManager(raises=RuntimeError("boom")))

    by_stage = {result.stage: result for result in controller.retrieve("deploy region us-east-1").stages}

    assert by_stage["fact_backend"].status == "unavailable"
    assert "fact_backend_search_failed:RuntimeError" in by_stage["fact_backend"].reason


def test_backend_without_search_is_reported_as_such():
    controller = make_controller(memory_manager=SearchlessManager())

    by_stage = {result.stage: result for result in controller.retrieve("deploy region us-east-1").stages}

    assert by_stage["fact_backend"].status == "unavailable"
    assert by_stage["fact_backend"].reason == "configured_backend_does_not_implement_search"


def test_health_reports_posture_and_never_claims_verification():
    controller = make_controller(session_engine=None)

    health = controller.health()

    states = {item.source: item.state for item in health}
    assert states["dream_rules"] == "configured"
    assert states["fact_backend"] == "configured"
    assert states["session_history"] == "unavailable"
    # Nothing here executes a write or a read, so no verification-grade claim.
    assert all(item.state in ("configured", "unavailable") for item in health)
    assert all(isinstance(item, MemorySourceHealth) for item in health)


def test_relevance_is_separate_from_truth_confidence():
    controller = make_controller()
    working_set = controller.retrieve("What AWS region does the deploy script use?")

    assert working_set.selected
    for item in working_set.selected:
        assert isinstance(item, RetrievedMemory)
        # A relevance score exists...
        assert 0.0 <= item.relevance <= 1.0
        # ...and the truth posture is a separate, honest field.
        assert item.evidence_state in ("explicit", "verified", "corroborated", "inferred", "unverified")


def test_curated_rule_reads_explicit_while_an_inferred_fact_does_not():
    curated = Candidate(id="d", memory_type="project_facts", subtype="curated_rule", content="x")
    confident = Candidate(id="a", memory_type="project_facts", subtype="context", content="x", authority=0.9)
    tentative = Candidate(id="b", memory_type="project_facts", subtype="context", content="x", authority=0.3)
    bare = Candidate(id="c", memory_type="project_facts", subtype="context", content="x")

    assert _evidence_state(curated) == "explicit"
    assert _evidence_state(confident) == "corroborated"
    assert _evidence_state(tentative) == "inferred"
    assert _evidence_state(bare) == "unverified"
    # Nothing is "verified": this controller never re-checks a current artefact.
    assert "verified" not in {_evidence_state(c) for c in (curated, confident, tentative, bare)}


def test_controller_abstains_when_no_source_matches():
    controller = make_controller(
        dream_store=FakeDreamStore(MEMORY_MD),
        memory_manager=FakeManager([]),
    )

    working_set = controller.retrieve("quantum chromodynamics lattice gauge renormalization")

    assert working_set.abstained is True
    assert working_set.selected == ()
    assert working_set.abstain_reason
    # Abstention is disclosed, not dressed up as a confident empty answer.
    assert working_set.text() == ""


def test_controller_abstains_on_a_too_short_query_rather_than_inventing_hits():
    working_set = make_controller().retrieve("ok")

    assert working_set.abstained is True
    assert working_set.abstain_reason == "query_too_short_to_retrieve"


def test_budget_is_refused_not_clamped():
    with pytest.raises(ValueError, match="positive number of tokens"):
        make_controller().retrieve("deploy region", budget=0)


def test_scope_is_the_resolved_user_and_travels_into_the_context():
    controller = make_controller(user_id="user-42", agent_name="planner")
    manager = controller._memory_manager

    controller.retrieve("deploy region us-east-1")

    assert manager.calls, "fact backend was not consulted"
    assert manager.calls[0]["user_id"] == "user-42"
    assert manager.calls[0]["agent_name"] == "planner"


def test_working_set_is_bounded_by_the_configured_budget():
    controller = make_controller(config=FusionConfig(enabled=True, total_budget_tokens=64))

    working_set = controller.retrieve("What AWS region does the deploy script use?")

    assert working_set.token_estimate <= 64


# ---------------------------------------------------------------------------
# Adapters in isolation
# ---------------------------------------------------------------------------


def test_dream_stage_maps_lines_to_bounded_candidates_with_line_numbers():
    from alpha.memory.fusion.query import TaskContext

    stage = _DreamRuleStage(FakeDreamStore(MEMORY_MD))
    ctx = TaskContext(scope_id="user-1", task_type="coding", budget=500)

    result = stage.retrieve("AWS_REGION deploy", "exact", 500, ctx)

    assert result.status == "ok"
    assert all(c.source_refs[0].startswith(str(FakeDreamStore(MEMORY_MD).memory_path)) for c in result.candidates)
    # The line number rides in the path:line source reference.
    assert all(_line_number(c) >= 2 for c in result.candidates)


def test_dream_stage_skips_headings_and_reports_empty_honestly():
    from alpha.memory.fusion.query import TaskContext

    stage = _DreamRuleStage(FakeDreamStore("# Long-Term Durable Memory\n\nCurated principles.\n"))
    ctx = TaskContext(scope_id="user-1", task_type="coding", budget=500)

    result = stage.retrieve("curated principles", "exact", 500, ctx)

    # Headings and the store preamble are structure, not memory records.
    assert result.status in ("empty", "ok")
    assert all(not c.content.startswith("#") for c in result.candidates)


def test_dream_stage_with_no_store_is_unavailable():
    from alpha.memory.fusion.query import TaskContext

    stage = _DreamRuleStage(None)
    ctx = TaskContext(scope_id="user-1", task_type="coding", budget=500)

    assert stage.retrieve("anything", "exact", 500, ctx).status == "unavailable"


def test_session_stage_maps_results_and_keeps_both_ids_as_refs():
    from alpha.memory.fusion.query import TaskContext

    engine = FakeSessionEngine([FakeEpisode("sess-9", "msg-3", "the pool size fix", 0.7, 1_700_000_000.0)])
    ctx = TaskContext(scope_id="user-1", task_type="coding", budget=500)

    result = _SessionHistoryStage(engine).retrieve("pool size", "exact", 500, ctx)

    assert result.status == "ok"
    candidate = result.candidates[0]
    assert "session:sess-9" in candidate.source_refs
    assert "message:msg-3" in candidate.source_refs
    assert candidate.event_time == 1_700_000_000.0


def test_fact_stage_categorises_into_budget_types():
    assert _memory_type_for("preference") == "identity_soul"
    assert _memory_type_for("correction") == "failures_lessons"
    assert _memory_type_for("procedure") == "procedures_skills"
    # An unmapped category falls into a funded type rather than inventing one.
    assert _memory_type_for("whatever") == "project_facts"


def test_significant_tokens_keep_case_and_drop_tiny_tokens():
    tokens = _significant_tokens("AWS_REGION and the Deploy.py path")
    assert "AWS_REGION" in tokens
    assert "Deploy.py" in tokens
    assert "and" not in tokens
    assert "the" not in tokens


# ---------------------------------------------------------------------------
# Phase 2: the hardened ActiveMemoryRouter
# ---------------------------------------------------------------------------


def test_router_carries_source_bearing_candidates(tmp_path: Path):
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text(MEMORY_MD, encoding="utf-8")

    result = ActiveMemoryRouter(memory_files=[mem_file], escalation_threshold=0.50).query("What is the AWS_REGION for the deploy script?")

    assert result.tier == 1
    assert result.candidates
    assert all(c.source == str(mem_file) for c in result.candidates)
    assert all(c.line_number >= 1 for c in result.candidates)
    assert all(0.0 < c.relevance <= 1.0 for c in result.candidates)


def test_router_tier2_no_longer_claims_a_fixed_confidence(tmp_path: Path):
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Core Memory\n- Some unrelated note about redis caching.\n", encoding="utf-8")

    def handler(q: str, lines: list[str]) -> str:
        return "Synthesized deep reasoning answer"

    result = ActiveMemoryRouter(memory_files=[mem_file], escalation_threshold=0.70).query("How did our security architecture evolve across sprints?", escalation_handler=handler)

    assert result.tier == 2 and result.escalated is True
    # A provider returning prose is not evidence, so no fabricated confidence.
    assert result.confidence == 0.0
    assert result.evidence_state == "unverified"


def test_router_empty_store_escalation_is_also_unverified(tmp_path: Path):
    empty = tmp_path / "MEMORY.md"
    empty.write_text("", encoding="utf-8")

    result = ActiveMemoryRouter(memory_files=[empty], escalation_threshold=0.70).query("anything at all", escalation_handler=lambda q, entries: "answer")

    assert result.tier == 2
    assert result.confidence == 0.0
    assert result.evidence_state == "unverified"


def test_router_does_not_report_a_tier2_answer_it_could_not_run(tmp_path: Path, caplog):
    """Inside a live event loop the sync path must not fake an escalation."""
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Core Memory\n- Some unrelated note about redis caching.\n", encoding="utf-8")
    router = ActiveMemoryRouter(memory_files=[mem_file], escalation_threshold=0.70)
    called: list[str] = []

    def handler(q: str, lines: list[str]) -> str:
        called.append(q)
        return "should not run"

    async def inside_loop() -> MemoryLookupResult:
        return router.query("How did our security architecture evolve across sprints?", escalation_handler=handler)

    result = asyncio.run(inside_loop())

    # The handler did not run, and the result says so instead of claiming tier 2.
    assert called == []
    assert result.escalated is False
    assert "query_async()" in result.reason


def test_router_query_async_escalates_from_inside_a_running_loop(tmp_path: Path):
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Core Memory\n- Some unrelated note about redis caching.\n", encoding="utf-8")
    router = ActiveMemoryRouter(memory_files=[mem_file], escalation_threshold=0.70)

    def handler(q: str, lines: list[str]) -> str:
        return "deep answer"

    async def inside_loop() -> MemoryLookupResult:
        return await router.query_async("How did our security architecture evolve across sprints?", escalation_handler=handler)

    result = asyncio.run(inside_loop())

    assert result.tier == 2
    assert result.escalated is True
    assert result.matches == ["deep answer"]
    assert result.confidence == 0.0


def test_router_ignores_process_working_directory(tmp_path: Path, monkeypatch):
    """Durable memory must not be reachable through the cwd."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "MEMORY.md").write_text("- Deploy script requires AWS_REGION=us-east-1.\n", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    router = ActiveMemoryRouter(memory_root=runtime_root, escalation_threshold=0.50)
    result = router.query("What is the AWS_REGION for deploy?")

    assert result.tier == 1
    assert result.candidates
    assert result.candidates[0].source == str(runtime_root / "MEMORY.md")


def test_router_bounds_an_overlong_query(tmp_path: Path):
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text(MEMORY_MD, encoding="utf-8")
    router = ActiveMemoryRouter(memory_files=[mem_file], max_query_chars=20)

    result = router.query("A" * 500)

    assert len(result.query) == 20


def test_dream_store_helper_pins_runtime_root(tmp_path: Path):
    from alpha.memory.active_controller import runtime_rooted_dream_store

    store = runtime_rooted_dream_store(tmp_path)

    assert isinstance(store, DreamStore)
    assert store.memory_path == tmp_path / "MEMORY.md"


def test_controller_module_exports_are_importable():
    import alpha.memory.active_controller as module

    for name in ("ActiveMemoryController", "WorkingSet", "RetrievedMemory", "MemorySourceHealth"):
        assert name in module.__all__
