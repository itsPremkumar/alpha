"""As-of (time-travel) memory_search read path (Hy-Memory adoption slice #3).

Design: the bi-temporal read primitive of Zep/Graphiti (``valid_at`` /
``invalid_at``) maps onto Alpha as a *query-time* projection: the caller may
ask what the memory system knew at time T. This slice wires ``as_of`` into the
one model-facing read surface (:func:`memory_search_tool`) and the fused
controller's built-in stages:

* the tool parses an ISO-8601 timestamp or unix-seconds value and REFUSES an
  unparseable one instead of silently ignoring it;
* with ``memory.fusion.enabled`` off there is no temporal semantics at all, so
  an ``as_of`` request is refused honestly rather than dropped;
* the fused read path excludes candidates whose knowledge is dated AFTER the
  cutoff (episode ``event_time``, fact ``valid_from``) — undated candidates
  are kept, because "unknown time" is not "future knowledge";
* the curated dream-rules file carries no per-line timestamps, so no rule is
  ever time-filtered there (disclosed in the module docstring).

Properties pinned here: parsed-or-refused parsing, tool-schema declaration,
fusion pass-through plus payload disclosure, stage time mapping, and the
central as-of filter with its honest empty-status disclosure.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from alpha.agents.memory import tools as memory_tools
from alpha.agents.memory.tools import _parse_as_of, memory_search_tool
from alpha.config.memory_config import MemoryConfig
from alpha.memory.active_controller import (
    ActiveMemoryController,
    _ConfiguredFactStage,
)
from alpha.memory.fusion.models import Candidate, StageResult
from alpha.memory.fusion.query import TaskContext

# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_as_of_accepts_iso_and_unix() -> None:
    assert _parse_as_of(None) is None
    assert _parse_as_of("") is None
    assert _parse_as_of("   ") is None
    assert _parse_as_of("1700000000") == pytest.approx(1700000000.0)
    assert _parse_as_of("1700000000.5") == pytest.approx(1700000000.5)
    assert _parse_as_of("2026-10-01T12:00:00Z") == pytest.approx(1790856000.0)
    assert _parse_as_of("2026-10-01T14:00:00+02:00") == pytest.approx(1790856000.0)


def test_parse_as_of_refuses_garbage() -> None:
    for bad in ("yesterday", "07:40", "2026-13-40"):
        with pytest.raises(ValueError):
            _parse_as_of(bad)


# ---------------------------------------------------------------------------
# tool surface
# ---------------------------------------------------------------------------


def test_tool_schema_declares_as_of() -> None:
    # tool_call_schema is the same generation path the model binding uses
    # (it excludes the runtime parameter) — mirrored from
    # scripts/check_tool_schemas.py.
    schema = memory_search_tool.tool_call_schema
    assert "as_of" in schema.model_json_schema().get("properties", {})


def _fusion_on(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> list[tuple[float | None]]:
    """Pin the memory config and capture controller.retrieve() as_of calls."""
    cfg = MemoryConfig.model_validate({"enabled": enabled, "fusion": {"enabled": True}})
    calls: list[tuple[float | None]] = []

    class FakeController:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def retrieve(self, query: str, *args: Any, **kwargs: Any) -> Any:
            calls.append(kwargs.get("as_of"))
            return SimpleNamespace(
                mode="hybrid",
                selected=[],
                abstained=True,
                abstain_reason="floor",
                degraded_sources=(),
                token_estimate=0,
            )

    import alpha.config.memory_config as config_module

    monkeypatch.setattr(config_module, "get_memory_config", lambda: cfg)
    monkeypatch.setattr(memory_tools, "get_memory_manager", lambda: __import__("types").SimpleNamespace())
    import alpha.memory.active_controller as controller_module

    monkeypatch.setattr(controller_module, "get_active_memory_controller", lambda *, user_id, agent_name: FakeController())
    return calls


def test_fusion_path_receives_as_of_and_discloses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fusion_on(monkeypatch, enabled=True)

    runtime = SimpleNamespace(context=None)
    result = memory_search_tool.func(runtime, "staging cluster", as_of="2026-10-01T00:00:00Z")

    payload = __import__("json").loads(result)
    assert calls and calls[-1] == pytest.approx(1790812800.0)
    assert payload.get("as_of") == pytest.approx(1790812800.0), "the cutoff is disclosed with the answer"


def test_no_as_of_keeps_the_payload_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fusion_on(monkeypatch, enabled=True)

    runtime = SimpleNamespace(context=None)
    result = memory_search_tool.func(runtime, "staging cluster")

    assert calls and calls[-1] is None
    assert "as_of" not in __import__("json").loads(result)


def test_as_of_with_fusion_gate_off_is_refused_not_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _fusion_on(monkeypatch, enabled=False)
    manager_calls: list[str] = []

    class NoSearchManager:
        def search(self, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
            manager_calls.append("search")
            return []

    import alpha.agents.memory.tools as tools_module

    monkeypatch.setattr(tools_module, "get_memory_manager", lambda: NoSearchManager())

    runtime = SimpleNamespace(context=None)
    result = memory_search_tool.func(runtime, "q", as_of="1700000000")

    assert "as_of" in result
    assert not manager_calls, "an unsupported parameter must not silently search"


def test_invalid_as_of_refuses_before_any_search(monkeypatch: pytest.MonkeyPatch) -> None:
    manager_calls: list[str] = []

    class NoSearchManager:
        def search(self, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
            manager_calls.append("search")
            return []

    import alpha.agents.memory.tools as tools_module

    monkeypatch.setattr(tools_module, "get_memory_manager", lambda: NoSearchManager())

    runtime = SimpleNamespace(context=None)
    result = memory_search_tool.func(runtime, "q", as_of="last Tuesday-ish")
    assert "as_of" in result
    assert not manager_calls


# ---------------------------------------------------------------------------
# stage time mapping
# ---------------------------------------------------------------------------


def test_fact_stage_maps_created_at_to_valid_from() -> None:
    rows = [
        {"id": "a", "content": "plain ISO date", "category": "context", "confidence": 0.8, "createdAt": "2026-09-01T00:00:00Z"},
        {"id": "b", "content": "numeric stamp", "category": "context", "createdAt": 1759000000.0},
        {"id": "c", "content": "no stamp", "category": "context"},
    ]
    stage = _ConfiguredFactStage(SimpleNamespace(search=lambda *args, **kwargs: rows), user_id="u", agent_name=None)
    ctx = TaskContext(scope_id="u", task_type="recall", budget=800)
    result = stage.retrieve("q", "hybrid", 800, ctx)

    by_id = {candidate.id: candidate.valid_from for candidate in result.candidates}
    assert by_id["fact:a"] == pytest.approx(1788220800.0)
    assert by_id["fact:b"] == pytest.approx(1759000000.0)
    assert by_id["fact:c"] is None


def test_parse_row_epoch_is_total() -> None:
    from alpha.memory.active_controller import _row_epoch

    assert _row_epoch("2026-03-01T00:00:00Z") == pytest.approx(1772323200.0)
    assert _row_epoch(1759000000.0) == pytest.approx(1759000000.0)
    assert _row_epoch("nonsense") is None
    assert _row_epoch(None) is None
    assert _row_epoch("") is None


# ---------------------------------------------------------------------------
# central as-of filter in ActiveMemoryController.retrieve
# ---------------------------------------------------------------------------


def _candidate(cid: str, *, event_time: float | None = None, valid_from: float | None = None) -> Candidate:
    return Candidate(id=cid, memory_type="recent_task_state", content=f"content {cid}", event_time=event_time, valid_from=valid_from)


class _FakeStage:
    def __init__(self, result: StageResult) -> None:
        self._result = result
        self.name = "fake"

    def retrieve(self, query: str, mode: str, budget: int, ctx: TaskContext) -> StageResult:
        self.ctx = ctx
        return self._result


def _controller_with(result: StageResult) -> tuple[ActiveMemoryController, _FakeStage]:
    stage = _FakeStage(result)
    # The relevance floor is not under test here (ranking lives in fusion);
    # zeroing it keeps the assertions entirely about the as-of projection.
    return ActiveMemoryController(stages=[stage], min_fused_score=0.0), stage


def test_retrieve_filters_future_dated_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    result = StageResult(
        stage="fake",
        status="ok",
        candidates=(
            _candidate("kept-old", event_time=100.0),
            _candidate("dropped-future", event_time=300.0),
            _candidate("kept-undated"),
            _candidate("kept-valid", valid_from=200.0),
        ),
        reason="fake_returned=4",
        latency_ms=0.0,
    )
    controller, stage = _controller_with(result)

    working = controller.retrieve("content", as_of=250.0)

    assert stage.ctx.as_of == pytest.approx(250.0), "the cutoff rides the task context"
    kept_ids = {candidate.memory_id for candidate in working.selected}
    assert kept_ids == {"kept-old", "kept-undated", "kept-valid"}


def test_retrieve_without_as_of_keeps_everything() -> None:
    result = StageResult(
        stage="fake",
        status="ok",
        candidates=(_candidate("a", event_time=999999.0), _candidate("b")),
        reason="fake_returned=2",
        latency_ms=0.0,
    )
    controller, stage = _controller_with(result)

    working = controller.retrieve("content")

    assert stage.ctx.as_of is None
    assert len(working.selected) >= 2


def test_retrieve_all_future_candidates_becomes_an_honest_stage_empty() -> None:
    result = StageResult(
        stage="fake",
        status="ok",
        candidates=(_candidate("future", valid_from=999999.0),),
        reason="fake_returned=1",
        latency_ms=0.0,
    )
    controller, _stage = _controller_with(result)

    working = controller.retrieve("content", as_of=500.0)

    empty_stages = [stage for stage in working.stages if stage.status == "empty"]
    assert empty_stages, "a fully filtered stage is reported empty, never ok"
    assert any("as_of" in (stage.reason or "") for stage in empty_stages), "the cutoff disclosure rides the reason"


def test_invalid_task_context_rejects_negative_as_of() -> None:
    with pytest.raises(ValidationError):
        TaskContext(scope_id="u", task_type="recall", budget=10, as_of=-1.0)
