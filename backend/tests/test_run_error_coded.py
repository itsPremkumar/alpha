"""End-to-end proof that a real run failure is emitted as a *coded* ``run.error``.

The audit that motivated ``alpha/errors`` found ``run.error`` in the event
catalog with zero consumers and an ``error_type`` that nothing could query. A
run that failed four months ago was only distinguishable by grepping prose in
``content``. These tests pin the other end of that: a real chain error now
carries a registry code, the severity/retry/recovery policy, and a correlation
id, on the persisted event *and* through the log/metric/recovery fan-out.

The contract that must not break is checked explicitly too: ``run.error``
content is still the exception string and ``metadata.error_type`` is still the
exception class name, because existing consumers read both.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest

from alpha.errors import ERROR_METRIC_NAME, classify, is_registered, report_exception, require_definition
from alpha.errors import report as report_module
from alpha.runtime.events.catalog import RUN_ERROR_EVENT
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal
from app.gateway.error_sse import UNBOUND_SSE_METRIC


@pytest.fixture
def journal():
    store = MemoryRunEventStore()
    instance = RunJournal("r1", "t1", store, flush_threshold=100)
    return instance, store


async def _events(store, event_type: str) -> list[dict]:
    events = await store.list_events("t1", "r1")
    return [event for event in events if event["event_type"] == event_type]


class TestRunErrorIsCoded:
    @pytest.mark.anyio
    async def test_run_error_carries_the_registry_policy(self, journal):
        instance, store = journal
        instance.on_chain_error(RuntimeError("graph blew up"), run_id=uuid4())
        await asyncio.sleep(0)
        await instance.flush()

        events = await _events(store, "run.error")
        assert len(events) == 1
        metadata = events[0]["metadata"]

        # The pre-existing contract fields are untouched.
        assert events[0]["content"] == "graph blew up"
        assert metadata["error_type"] == "RuntimeError"

        # The new coded fields, all sourced from the single registry.
        assert metadata["error_code"] == "RUN_EXECUTION_FAILED"
        assert metadata["severity"] == require_definition("RUN_EXECUTION_FAILED").severity.value
        assert metadata["retryable"] is require_definition("RUN_EXECUTION_FAILED").retryable
        assert metadata["error_message"] == require_definition("RUN_EXECUTION_FAILED").message
        assert metadata["error_correlation_id"] == "alpha.errors.run"
        assert metadata["recovery"] == "investigate"

    @pytest.mark.anyio
    async def test_a_recognisable_exception_keeps_its_specific_code(self, journal):
        """A timeout must not be filed as a generic internal error."""
        instance, store = journal
        instance.on_chain_error(TimeoutError("provider did not answer"), run_id=uuid4())
        await asyncio.sleep(0)
        await instance.flush()

        events = await _events(store, "run.error")
        assert events[0]["metadata"]["error_code"] == "TIMEOUT"
        assert events[0]["metadata"]["retryable"] is True

    @pytest.mark.anyio
    async def test_database_outage_keeps_its_persistence_code(self, journal):
        instance, store = journal
        instance.on_chain_error(sqlite3.OperationalError("database is locked"), run_id=uuid4())
        await asyncio.sleep(0)
        await instance.flush()

        events = await _events(store, "run.error")
        assert events[0]["metadata"]["error_code"] == "PERSISTENCE_UNAVAILABLE"
        assert events[0]["metadata"]["severity"] == "critical"

    @pytest.mark.anyio
    async def test_user_message_is_registry_owned(self, journal):
        """The user-facing wording comes from the registry, not from str(exc)."""
        instance, store = journal
        instance.on_chain_error(RuntimeError("secret token abc123 leaked"), run_id=uuid4())
        await asyncio.sleep(0)
        await instance.flush()

        events = await _events(store, "run.error")
        assert "abc123" not in events[0]["metadata"]["error_message"]

    @pytest.mark.anyio
    async def test_run_id_travels_so_a_failure_is_traceable(self, journal):
        instance, store = journal
        run_id = uuid4()
        instance.on_chain_error(RuntimeError("boom"), run_id=run_id)
        await asyncio.sleep(0)
        await instance.flush()
        events = await _events(store, "run.error")
        assert events[0]["run_id"] == "r1"

    @pytest.mark.anyio
    async def test_coded_error_keeps_its_own_code_through_the_journal(self, journal):
        from alpha.errors import CodedError

        instance, store = journal
        instance.on_chain_error(CodedError("SANDBOX_POLICY_DENIED", detail="egress blocked"), run_id=uuid4())
        await asyncio.sleep(0)
        await instance.flush()
        events = await _events(store, "run.error")
        assert events[0]["metadata"]["error_code"] == "SANDBOX_POLICY_DENIED"
        assert events[0]["metadata"]["error_type"] == "CodedError"


class TestRunErrorFansOut:
    @pytest.mark.anyio
    async def test_the_failure_is_counted_in_the_registry_metric(self, journal):
        from alpha.ops.metrics import get_metrics_registry

        instance, _store = journal
        instance.on_chain_error(RuntimeError("boom"), run_id=uuid4())
        await asyncio.sleep(0)
        text = get_metrics_registry().render_prometheus()
        assert ERROR_METRIC_NAME in text
        assert 'code="RUN_EXECUTION_FAILED"' in text

    @pytest.mark.anyio
    async def test_the_failure_reaches_the_recovery_ledger(self, journal):
        instance, _store = journal
        report_module.reset_error_reporter()
        try:
            reporter = report_module.configure_error_reporter(log_sink=lambda _r: None)
            instance.on_chain_error(RuntimeError("boom"), run_id=uuid4())
            await asyncio.sleep(0)
            codes = [record.code for record in reporter.ledger.records()]
            assert "RUN_EXECUTION_FAILED" in codes
            assert reporter.ledger.pending(), "an unhandled run failure must still be pending"
        finally:
            report_module.reset_error_reporter()

    @pytest.mark.anyio
    async def test_the_failure_is_logged_at_the_code_severity(self, journal, caplog):
        instance, _store = journal
        with caplog.at_level("ERROR", logger="alpha.errors.report"):
            instance.on_chain_error(RuntimeError("boom"), run_id=uuid4())
            await asyncio.sleep(0)
        records = [r for r in caplog.records if r.name == "alpha.errors.report"]
        assert records, "a terminal run failure must reach the log"
        assert any("code=RUN_EXECUTION_FAILED" in r.getMessage() for r in records)
        assert any(r.levelname == "ERROR" for r in records)

    @pytest.mark.anyio
    async def test_a_failing_run_still_ends_its_run(self, journal):
        """Wiring must not change the lifecycle: run.end must still be written."""
        instance, store = journal
        root = uuid4()
        instance.on_chain_start({}, {}, run_id=root, parent_run_id=None)
        instance.on_chain_error(RuntimeError("boom"), run_id=root)
        instance.on_chain_end({"messages": []}, run_id=root, parent_run_id=None)
        await asyncio.sleep(0)
        await instance.flush()

        events = await store.list_events("t1", "r1")
        types = {event["event_type"] for event in events}
        assert "run.error" in types
        assert "run.end" in types
        assert types <= {RUN_ERROR_EVENT.event_type, "run.start", "run.end"}


class TestErrorReporterSseLeg:
    """The fan-out's fourth leg, bound to the run that caused the failure.

    Until the Gateway bound ``configure_error_reporter(sse_sink=...)`` the log,
    metric and recovery legs fired for every coded run failure and the SSE leg
    fired for none: a report counted as unbound against a process-global nobody
    read. These cases drive the real journal, the real reporter and the real
    memory bridge, because a stubbed bridge would pass even if the frame shape
    were wrong in the one field clients read.
    """

    @staticmethod
    def _bind(bridge):
        from types import SimpleNamespace

        from app.gateway.error_sse import bind_error_reporter_sse_leg

        app = SimpleNamespace(state=SimpleNamespace())
        bind_error_reporter_sse_leg(app, bridge, loop=asyncio.get_running_loop())
        return app

    @pytest.mark.anyio
    async def test_a_reported_run_failure_reaches_the_runs_stream(self):
        from alpha.runtime.stream_bridge.memory import MemoryStreamBridge

        report_module.reset_error_reporter()
        bridge = MemoryStreamBridge()
        app = self._bind(bridge)
        try:
            store = MemoryRunEventStore()
            instance = RunJournal("r1", "t1", store, flush_threshold=100)
            run_id = uuid4()
            instance.on_chain_error(RuntimeError("boom"), run_id=run_id)
            # The sink hands the publish to the serving loop; give it a turn.
            await asyncio.sleep(0.05)

            frames = []
            async for item in bridge.subscribe(str(run_id)):
                frames.append(item)
                break

            assert frames, "the bound leg must publish onto the run's own stream"
            assert frames[0].event == "error"
            data = frames[0].data
            # `code`/`correlation_id` are the keys the SSE consumer reads; the
            # reporter payload nests them under `error_*`, so a verbatim payload
            # would reach every client as an unidentifiable failure.
            assert data["code"] == "RUN_EXECUTION_FAILED"
            assert data["correlation_id"] == "alpha.errors.run"
            assert data["run_id"] == str(run_id)
            assert data["source"] == "error_reporter"
            # The policy travels verbatim rather than being re-derived, so the
            # frame cannot disagree with the registry the journal wrote.
            definition = require_definition("RUN_EXECUTION_FAILED")
            assert data["retryable"] is definition.retryable
            assert data["severity"] == definition.severity.value
            assert data["recovery"] == definition.recovery.value
            # Registry-owned prose is rendered; the exception string is an
            # operator field (log + recovery ledger) and never a client frame.
            assert data["message"] == require_definition("RUN_EXECUTION_FAILED").message
            assert "boom" not in json.dumps(data)
            # The leg is bound, so the reporter's own unbound counter stays 0.
            assert report_module.get_error_reporter().unbound_sse == 0
            assert app.state.error_reporter_sse["published"] == 1
        finally:
            report_module.reset_error_reporter()

    @pytest.mark.anyio
    async def test_a_report_with_no_run_id_is_counted_and_publishes_nothing(self):
        """No run means no stream: a measurable gap, not a silent drop."""
        from alpha.ops.metrics import get_metrics_registry
        from alpha.runtime.stream_bridge.memory import MemoryStreamBridge

        report_module.reset_error_reporter()
        bridge = MemoryStreamBridge()
        app = self._bind(bridge)
        try:
            report_module.get_error_reporter().report("TIMEOUT", detail="provider did not answer")
            await asyncio.sleep(0.05)

            assert app.state.error_reporter_sse["published"] == 0
            assert app.state.error_reporter_sse["unbound"] == 1
            # The other three legs still ran: an unaddressable report is
            # recorded, not swallowed.
            reporter = report_module.get_error_reporter()
            assert reporter.ledger.pending(), "the recovery leg still records it"
            assert [record.code for record in reporter.ledger.records()] == ["TIMEOUT"]
            rendered = get_metrics_registry().render_prometheus()
            assert UNBOUND_SSE_METRIC in rendered
            # Nothing was published, so no run stream exists at all.
            assert not await bridge.stream_exists("no-such-run")
        finally:
            report_module.reset_error_reporter()

    def test_the_gateway_lifespan_binds_the_leg_where_the_bridge_is_built(self):
        """A binding added after the first run exists would miss that run."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app" / "gateway" / "deps.py").read_text(encoding="utf-8")
        bridge_at = source.index("app.state.stream_bridge = await stack.enter_async_context(make_stream_bridge(config))")
        bind_at = source.index("bind_error_reporter_sse_leg(app,")
        assert bind_at > bridge_at, "the sink addresses the bridge, so it binds after the bridge exists"
        assert source.index("asyncio.get_running_loop()", bind_at) > bind_at


class TestGateAndRegistryAgree:
    @pytest.mark.parametrize(
        "exc",
        [RuntimeError("x"), TimeoutError("slow"), sqlite3.OperationalError("database is locked"), ValueError("bad")],
    )
    def test_a_persisted_code_is_always_a_registered_code(self, exc):
        assert is_registered(classify(exc).code), "a run must never persist an unregistered code"

    def test_the_journal_routes_through_the_registry(self):
        """The run fallback must resolve through the registry, not a bare literal."""
        from alpha.runtime import journal as journal_module

        assert callable(journal_module.classify)
        assert journal_module.classify is classify
        assert journal_module.report_exception is report_exception
        assert require_definition("RUN_EXECUTION_FAILED").code == "RUN_EXECUTION_FAILED"
