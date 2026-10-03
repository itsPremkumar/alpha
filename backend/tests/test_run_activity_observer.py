"""The run-lifecycle observer is read-only, total, and cannot fail a run.

`RunManager` is the sole run lifecycle owner, so the observer added for group
status displays has to be provably incapable of changing a transition. These
are the properties that make that claim true rather than asserted.
"""

from __future__ import annotations

import pytest

import alpha.runtime.runs.manager as manager_mod
from alpha.runtime.runs import set_activity_observer
from alpha.runtime.runs.manager import RunStatus


@pytest.fixture(autouse=True)
def _clear_observer():
    set_activity_observer(None)
    yield
    set_activity_observer(None)


class _Record:
    run_id = "run-1"


def test_no_observer_is_the_default_cost():
    """Every other deployment pays one module-global lookup and nothing else."""
    assert manager_mod._activity_observer is None


def test_the_observer_receives_the_event_name():
    seen: list[tuple[str, str]] = []
    set_activity_observer(lambda record, event: seen.append((record.run_id, event)))
    manager_mod._notify_activity(_Record(), "group.activity.terminal")
    assert seen == [("run-1", "group.activity.terminal")]


def test_an_observer_that_raises_is_swallowed():
    """A display bug must never be able to fail a run."""

    def boom(record, event):
        raise RuntimeError("display exploded")

    set_activity_observer(boom)
    manager_mod._notify_activity(_Record(), "group.activity.terminal")


def test_the_observer_can_be_cleared():
    seen: list[str] = []
    set_activity_observer(lambda record, event: seen.append(event))
    set_activity_observer(None)
    manager_mod._notify_activity(_Record(), "group.activity.terminal")
    assert seen == []


class TestStartAndTerminalAreReported:
    @pytest.mark.asyncio
    async def test_a_started_run_publishes_started(self):
        seen: list[str] = []
        set_activity_observer(lambda record, event: seen.append(event))
        manager = manager_mod.RunManager()
        record = await manager.create_or_reject("thread-1")
        assert await manager.try_start(record.run_id) == manager_mod.RunStartOutcome.started
        assert "group.activity.started" in seen

    @pytest.mark.asyncio
    async def test_a_terminal_run_publishes_terminal_with_its_stop_reason(self):
        seen: list[tuple[str, str | None]] = []
        set_activity_observer(lambda record, event: seen.append((event, record.stop_reason)))
        manager = manager_mod.RunManager()
        record = await manager.create_or_reject("thread-2")
        await manager.try_start(record.run_id)
        await manager.set_status(record.run_id, RunStatus.error, stop_reason="orphan_recovered", error="lease expired")
        terminal = [e for e in seen if e[0] == "group.activity.terminal"]
        assert terminal and terminal[-1][1] == "orphan_recovered"


class TestOwnershipLossIsACrashSignal:
    @pytest.mark.asyncio
    async def test_losing_the_lease_publishes_ownership_lost(self):
        """Losing the lease is the strongest death evidence available: this
        worker is no longer authorized to publish an outcome and a peer owns
        terminalization."""
        seen: list[str] = []
        set_activity_observer(lambda record, event: seen.append(event))
        manager = manager_mod.RunManager()
        record = await manager.create_or_reject("thread-3")
        await manager.try_start(record.run_id)
        assert await manager._mark_ownership_lost(record, reason="lease expired", require_active=False) is True
        assert "group.activity.ownership_lost" in seen


class TestGatewayBinding:
    def test_a_run_with_no_bot_is_not_recorded(self):
        """An ordinary chat run is nobody's work in this sense; attributing it
        to a room member would put a status dot on an agent that did nothing."""
        from app.gateway.deps import _activity_bot_name, _record_run_activity

        class Chat:
            run_id = "r"
            metadata = {}
            status = RunStatus.success
            stop_reason = None
            error = None

        assert _activity_bot_name(Chat()) is None
        _record_run_activity(Chat(), "group.activity.terminal")  # must not raise

    @pytest.mark.parametrize("key", ["bot_name", "agent_name", "member"])
    def test_each_accepted_binding_key_resolves(self, key):
        from app.gateway.deps import _activity_bot_name

        class Bot:
            run_id = "r"
            metadata = {key: "  Coder "}
            status = RunStatus.running
            stop_reason = None
            error = None

        assert _activity_bot_name(Bot()) == "coder"

    def test_a_non_dict_metadata_is_ignored(self):
        from app.gateway.deps import _activity_bot_name

        class Broken:
            run_id = "r"
            metadata = ["not", "a", "dict"]

        assert _activity_bot_name(Broken()) is None
