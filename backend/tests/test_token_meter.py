"""Tests for the process-local cumulative token ledger and its middleware wiring."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from alpha.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from alpha.runtime.token_meter import (
    TokenMeter,
    TokenSnapshot,
    check_token_budget,
    default_meter,
    meter_snapshot,
    record_token_usage,
    reset_token_meter,
)


@pytest.fixture(autouse=True)
def _reset_global_meter():
    reset_token_meter()
    yield
    reset_token_meter()


def _usage(input_tokens: int = 100, output_tokens: int = 50) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}


def _runtime(*, thread_id: str = "t1", user_id: str = "u1", model: str = "m1"):
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id, "user_id": user_id}
    runtime.config = {"metadata": {"model_name": model}}
    return runtime


class TestTokenMeter:
    def test_record_and_snapshot_totals(self):
        meter = TokenMeter()
        meter.record(user_id="u", thread_id="t", model="m", input_tokens=100, output_tokens=50)
        meter.record(user_id="u", thread_id="t", model="m", input_tokens=10, output_tokens=5)
        snapshot = meter.snapshot(user_id="u", thread_id="t", model="m")
        assert snapshot == TokenSnapshot(input_tokens=110, output_tokens=55, total_tokens=165, runs=2, scopes=1)

    def test_snapshot_filters_are_wildcards_by_default(self):
        meter = TokenMeter()
        meter.record(user_id="u1", thread_id="t1", model="m", input_tokens=10, output_tokens=0)
        meter.record(user_id="u2", thread_id="t1", model="m", input_tokens=20, output_tokens=0)
        assert meter.snapshot().total_tokens == 30
        assert meter.snapshot(user_id="u1").total_tokens == 10
        assert meter.snapshot(thread_id="t1").scopes == 2
        assert meter.snapshot(user_id="nobody").total_tokens == 0

    def test_malformed_input_coerces_instead_of_raising(self):
        meter = TokenMeter()
        meter.record(user_id=None, thread_id=123, model="", input_tokens=-5, output_tokens="lots")
        snapshot = meter.snapshot(user_id="unknown", thread_id="unknown", model="unknown")
        assert snapshot.total_tokens == 0
        assert snapshot.runs == 1

    def test_scope_cap_evicts_oldest(self):
        meter = TokenMeter(max_scopes=2)
        meter.record(user_id="u1", input_tokens=10)
        meter.record(user_id="u2", input_tokens=20)
        meter.record(user_id="u3", input_tokens=30)
        assert meter.scope_count == 2
        assert meter.snapshot().total_tokens == 50
        with pytest.raises(ValueError):
            TokenMeter(max_scopes=0)

    def test_check_budget(self):
        meter = TokenMeter()
        meter.record(user_id="u", input_tokens=80, output_tokens=20)
        within = meter.check_budget(1000, user_id="u")
        assert within == {"max_total_tokens": 1000, "used": 100, "remaining": 900, "exceeded": False}
        over = meter.check_budget(100, user_id="u")
        assert over["exceeded"] is True
        assert over["remaining"] == 0
        assert meter.check_budget(0, user_id="u")["exceeded"] is True

    def test_reset_scoped(self):
        meter = TokenMeter()
        meter.record(user_id="u1", input_tokens=10)
        meter.record(user_id="u2", input_tokens=20)
        assert meter.reset(user_id="u1") == 1
        assert meter.snapshot().total_tokens == 20
        assert meter.reset() == 1
        assert meter.snapshot().runs == 0

    def test_concurrent_records_do_not_lose_counts(self):
        meter = TokenMeter()
        threads = [threading.Thread(target=lambda: [meter.record(user_id="u", input_tokens=1) for _ in range(200)]) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert meter.snapshot(user_id="u").total_tokens == 1600


class TestGlobalMeterHelpers:
    def test_helpers_share_default_meter(self):
        record_token_usage(user_id="u", thread_id="t", model="m", input_tokens=7, output_tokens=3)
        snapshot = meter_snapshot(user_id="u")
        assert (snapshot.input_tokens, snapshot.output_tokens) == (7, 3)
        assert check_token_budget(9, user_id="u")["exceeded"] is True
        assert default_meter().scope_count == 1


class TestMiddlewareWiring:
    def test_after_model_records_response_totals(self):
        middleware = TokenUsageMiddleware()
        message = AIMessage(content="done", usage_metadata=_usage(350, 240))
        result = middleware.after_model({"messages": [message]}, _runtime())
        assert result is not None
        snapshot = meter_snapshot(user_id="u1", thread_id="t1", model="m1")
        assert (snapshot.input_tokens, snapshot.output_tokens, snapshot.runs) == (350, 240, 1)

    def test_recording_is_side_effect_only(self):
        middleware = TokenUsageMiddleware()
        message = AIMessage(content="done", usage_metadata=_usage())
        first = middleware.after_model({"messages": [message]}, _runtime())
        # Same state shape as without the meter: only the attribution update.
        assert first is not None
        assert meter_snapshot().runs == 1

    def test_no_usage_metadata_records_nothing(self):
        middleware = TokenUsageMiddleware()
        middleware.after_model({"messages": [AIMessage(content="no usage")]}, _runtime())
        assert meter_snapshot().runs == 0

    def test_injected_meter_is_used(self):
        meter = TokenMeter()
        middleware = TokenUsageMiddleware(meter=meter)
        message = AIMessage(content="done", usage_metadata=_usage(10, 5))
        middleware.after_model({"messages": [message]}, _runtime(thread_id="tx"))
        assert meter.snapshot(thread_id="tx").total_tokens == 15
        assert meter_snapshot().runs == 0

    def test_async_path_records(self):
        import asyncio

        middleware = TokenUsageMiddleware()
        message = AIMessage(content="done", usage_metadata=_usage(10, 5))
        result = asyncio.run(middleware.aafter_model({"messages": [message]}, _runtime()))
        assert result is not None
        assert meter_snapshot().runs == 1
