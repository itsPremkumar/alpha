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
import sqlite3
from uuid import uuid4

import pytest

from alpha.errors import ERROR_METRIC_NAME, classify, is_registered, report_exception, require_definition
from alpha.errors import report as report_module
from alpha.runtime.events.catalog import RUN_ERROR_EVENT
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal


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
