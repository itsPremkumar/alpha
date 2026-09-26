"""Per-run token usage + estimated cost: ``GET /api/threads/{tid}/runs/{rid}/usage``.

The run record already carried run-level totals and a per-model token split, and
``RunJournal`` already persisted every model call's ``metadata.usage`` on its
``llm.ai.response`` event (plus a delegated execution's cumulative usage on its
terminal ``subagent.end``). Nothing exposed that per call, so a finished run
could show a count but not its spend.

These tests pin the additive pieces:
- per-model rows, sourced honestly (``per_model`` / ``run_totals`` / ``unavailable``)
- one row per model call, read through the same ``after_seq`` cursor the run's
  event stream already supports
- a terminal ``subagent.end`` without usage stays absent instead of becoming a
  zero-token row
- a bounded page walk reports ``calls_complete=False`` rather than a short list
  presented as the whole run
- cost is ``null`` unless ``models[*].pricing`` is configured, and is computed
  with the console's cache-aware pricing helpers
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from alpha.runtime import DisconnectMode, RunRecord, RunStatus
from alpha.runtime.events.store.memory import MemoryRunEventStore
from app.gateway.routers import thread_runs
from app.gateway.routers.console import _ModelPricing

THREAD = "thread-usage"
RUN = "run-usage"


def _record(**overrides: Any) -> RunRecord:
    base: dict[str, Any] = {
        "run_id": RUN,
        "thread_id": THREAD,
        "assistant_id": None,
        "status": RunStatus.success,
        "on_disconnect": DisconnectMode.cancel,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:30+00:00",
        "model_name": "vendor/model-a",
        "error": None,
    }
    base.update(overrides)
    return RunRecord(**base)


def _make_app(record: RunRecord, event_store: Any) -> Any:
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    run_manager = MagicMock()
    run_manager.get = AsyncMock(return_value=record)
    app.state.run_manager = run_manager
    app.state.run_event_store = event_store
    return app


def _seed(*events: tuple[str, str, Any, dict]) -> MemoryRunEventStore:
    """Write run events through the real memory store (one loop, ordered seqs)."""
    store = MemoryRunEventStore()

    async def _write() -> None:
        for event_type, category, content, metadata in events:
            await store.put(thread_id=THREAD, run_id=RUN, event_type=event_type, category=category, content=content, metadata=metadata)

    asyncio.run(_write())
    return store


def test_run_usage_reports_per_model_and_per_call_rows():
    store = _seed(
        (
            "llm.ai.response",
            "message",
            {"type": "ai", "content": "hi", "response_metadata": {"model_name": "vendor/model-a"}},
            {
                "caller": "lead_agent",
                "llm_call_index": 1,
                "latency_ms": 1200,
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "input_token_details": {"cache_read": 40}},
            },
        ),
        (
            "llm.ai.response",
            "message",
            {"type": "ai", "content": "tool call", "response_metadata": {"model_name": "vendor/model-b"}},
            {
                "caller": "middleware:title_generation",
                "llm_call_index": 2,
                "usage": {"input_tokens": 5, "output_tokens": 1, "total_tokens": 0},
            },
        ),
        (
            "subagent.end",
            "subagent",
            {"task_id": "task-1", "status": "completed", "model_name": "vendor/model-c", "usage": {"input_tokens": 500, "output_tokens": 80, "total_tokens": 580}},
            {"task_id": "task-1"},
        ),
        # A non-usage event in the same run must never become a usage row.
        ("run.start", "trace", {"chain": "agent"}, {"caller": "lead_agent"}),
    )

    record = _record(
        total_input_tokens=605,
        total_output_tokens=101,
        total_tokens=726,
        llm_call_count=2,
        lead_agent_tokens=120,
        subagent_tokens=580,
        middleware_tokens=26,
        token_usage_by_model={
            "vendor/model-a": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cache_read_tokens": 40},
            "vendor/model-c": {"input_tokens": 500, "output_tokens": 80, "total_tokens": 580},
            "unknown": {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11},
        },
    )
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN
    assert body["total_tokens"] == 726
    assert body["llm_call_count"] == 2
    assert body["by_model_source"] == "per_model"
    assert [row["model"] for row in body["by_model"]] == ["unknown", "vendor/model-a", "vendor/model-c"]
    assert body["by_model"][1]["cache_read_tokens"] == 40
    assert body["by_model"][2]["total_tokens"] == 580

    assert body["calls_complete"] is True
    assert [row["source"] for row in body["calls"]] == ["llm_response", "llm_response", "subagent"]

    first = body["calls"][0]
    assert first["caller"] == "lead_agent"
    assert first["model"] == "vendor/model-a"
    assert (first["input_tokens"], first["output_tokens"], first["total_tokens"]) == (100, 20, 120)
    assert first["cache_read_tokens"] == 40
    assert first["latency_ms"] == 1200
    assert first["call_index"] == 1
    assert first["seq"] is not None

    # total_tokens 0 falls back to input + output, exactly like the journal.
    second = body["calls"][1]
    assert second["caller"] == "middleware:title_generation"
    assert (second["input_tokens"], second["output_tokens"], second["total_tokens"]) == (5, 1, 6)
    assert second["cache_read_tokens"] is None

    sub = body["calls"][2]
    assert sub["source"] == "subagent"
    assert sub["task_id"] == "task-1"
    assert sub["status"] == "completed"
    assert sub["model"] == "vendor/model-c"
    assert sub["total_tokens"] == 580


def test_run_usage_skips_subagent_end_without_usage():
    """An unreported usage snapshot stays absent, never a zero-token row."""
    store = _seed(("subagent.end", "subagent", {"task_id": "task-2", "status": "failed", "error": "boom"}, {"task_id": "task-2"}))

    record = _record(status=RunStatus.error, error="boom", total_tokens=0, total_input_tokens=0, total_output_tokens=0)
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["calls"] == []
    assert body["by_model"] == []
    assert body["by_model_source"] == "unavailable"
    assert body["calls_complete"] is True
    assert body["total_cost"] is None
    assert body["currency"] is None
    assert body["pricing_configured"] is False


def test_run_usage_falls_back_to_run_totals_without_a_per_model_split():
    store = MemoryRunEventStore()
    record = _record(total_input_tokens=12, total_output_tokens=3, total_tokens=15, llm_call_count=1)
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    body = response.json()
    assert body["by_model_source"] == "run_totals"
    assert body["by_model"] == [{"model": "vendor/model-a", "input_tokens": 12, "output_tokens": 3, "total_tokens": 15, "cache_read_tokens": None, "cost": None}]


def test_run_usage_prices_calls_and_totals_from_configured_pricing(monkeypatch: pytest.MonkeyPatch):
    store = _seed(
        (
            "llm.ai.response",
            "message",
            {"type": "ai", "content": "hi", "response_metadata": {"model_name": "vendor/model-a"}},
            {"caller": "lead_agent", "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000, "input_token_details": {"cache_read": 400_000}}},
        )
    )
    pricing = {"vendor/model-a": _ModelPricing(2.0, 6.0, "USD", 0.5)}
    monkeypatch.setattr(thread_runs, "_build_pricing_map", lambda: pricing)

    record = _record(
        total_input_tokens=1_000_000,
        total_output_tokens=1_000_000,
        total_tokens=2_000_000,
        llm_call_count=1,
        token_usage_by_model={"vendor/model-a": {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000, "cache_read_tokens": 400_000}},
    )
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    body = response.json()
    assert body["pricing_configured"] is True
    assert body["currency"] == "USD"
    # 600k uncached input @2.0 + 400k cache-hit input @0.5 + 1M output @6.0
    assert body["total_cost"] == round(600_000 / 1e6 * 2.0 + 400_000 / 1e6 * 0.5 + 1e6 / 1e6 * 6.0, 6)
    assert body["by_model"][0]["cost"] == body["total_cost"]
    assert body["calls"][0]["cost"] == body["total_cost"]


def test_run_usage_leaves_cost_null_for_unpriced_models(monkeypatch: pytest.MonkeyPatch):
    """Pricing configured for other models must not fabricate this run's cost."""
    store = _seed(
        (
            "llm.ai.response",
            "message",
            {"type": "ai", "content": "hi", "response_metadata": {"model_name": "vendor/unpriced"}},
            {"caller": "lead_agent", "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
        )
    )
    monkeypatch.setattr(thread_runs, "_build_pricing_map", lambda: {"vendor/other": _ModelPricing(1.0, 2.0, "USD")})

    record = _record(
        model_name="vendor/unpriced",
        total_input_tokens=10,
        total_output_tokens=2,
        total_tokens=12,
        llm_call_count=1,
        token_usage_by_model={"vendor/unpriced": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
    )
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    body = response.json()
    assert body["pricing_configured"] is True
    assert body["currency"] == "USD"
    assert body["total_cost"] is None
    assert body["by_model"][0]["cost"] is None
    assert body["calls"][0]["cost"] is None


def test_run_usage_marks_the_call_list_partial_when_the_page_walk_is_bounded():
    """A run with more usage events than the bounded walk covers says so."""

    class NeverEndingStore:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self._next_seq = 0

        async def list_events(self, thread_id, run_id, *, event_types=None, task_id=None, limit=500, after_seq=None):
            self.calls.append({"event_types": list(event_types or []), "limit": limit, "after_seq": after_seq})
            page = []
            for _ in range(limit):
                self._next_seq += 1
                page.append(
                    {
                        "seq": self._next_seq,
                        "event_type": "llm.ai.response",
                        "category": "message",
                        "content": {"response_metadata": {"model_name": "vendor/model-a"}},
                        "metadata": {"caller": "lead_agent", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
                    }
                )
            return page

    store = NeverEndingStore()
    record = _record(total_input_tokens=10, total_output_tokens=10, total_tokens=20, llm_call_count=1)
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    body = response.json()
    assert body["calls_complete"] is False
    assert len(body["calls"]) == thread_runs.USAGE_EVENT_PAGE_SIZE * thread_runs.USAGE_EVENT_MAX_PAGES
    # The walk pages forward with the same after_seq cursor the events endpoint uses.
    assert store.calls[0]["after_seq"] is None
    assert store.calls[1]["after_seq"] == thread_runs.USAGE_EVENT_PAGE_SIZE
    assert store.calls[0]["event_types"] == ["llm.ai.response", "subagent.end"]
    assert len(store.calls) == thread_runs.USAGE_EVENT_MAX_PAGES


def test_run_usage_stops_paging_when_a_page_lacks_a_usable_cursor():
    """No usable ``seq`` must not re-read page one forever."""

    class StuckStore:
        def __init__(self) -> None:
            self.calls = 0

        async def list_events(self, thread_id, run_id, *, event_types=None, task_id=None, limit=500, after_seq=None):
            self.calls += 1
            return [
                {
                    "seq": None,
                    "event_type": "llm.ai.response",
                    "category": "message",
                    "content": {},
                    "metadata": {"usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}},
                }
            ] * limit

    store = StuckStore()
    record = _record(total_input_tokens=4, total_output_tokens=1, total_tokens=5, llm_call_count=1)
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    body = response.json()
    assert store.calls == 1
    assert body["calls_complete"] is False
    assert len(body["calls"]) == thread_runs.USAGE_EVENT_PAGE_SIZE
    assert body["calls"][0]["seq"] is None
    assert body["calls"][0]["total_tokens"] == 4


def test_run_usage_404s_for_a_run_belonging_to_another_thread():
    store = MemoryRunEventStore()
    record = _record(thread_id="other-thread")
    app = _make_app(record, store)
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{RUN}/usage")

    assert response.status_code == 404
    assert response.json()["detail"] == f"Run {RUN} not found"


def test_run_usage_404s_when_the_run_does_not_exist():
    app = _make_app(None, MemoryRunEventStore())
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/missing/usage")

    assert response.status_code == 404


def test_run_response_exposes_the_per_model_split_error_and_model():
    record = _record(
        error="provider exploded",
        token_usage_by_model={"vendor/model-a": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}},
    )
    body = thread_runs._record_to_response(record)
    assert body.model == "vendor/model-a"
    assert body.error == "provider exploded"
    assert body.token_usage_by_model == {"vendor/model-a": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}}

    # A run that never reported a split is ``None`` (unknown), not ``{}``/zero.
    empty = thread_runs._record_to_response(_record())
    assert empty.token_usage_by_model is None
    assert empty.error is None
    assert empty.total_tokens == 0
