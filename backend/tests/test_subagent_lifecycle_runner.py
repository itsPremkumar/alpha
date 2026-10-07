"""The lifecycle runner must execute for real and never fabricate a terminal state.

Phase 1, task 3.T2 of `docs/FEATURE_COMPLETION_PLAN.md`.

## The defect this closes

`SubagentLifecycleManager` had a complete lifecycle and a Gateway surface that
read it, but `start_subagent` had **no production caller**. A record created by
`POST /api/subagents/control/spawn` therefore sat at `ready` forever. Measured on
the live record that route returned:

    status 'ready'   started_at None   completed_at None
    last_heartbeat None   renew_count 0   result None

## What these tests pin

The failure modes that matter are not crashes, they are **plausible lies**:

* a FAILED / CANCELLED / TIMED_OUT execution reported as `completed`
* an exception during assembly or submission leaving the record `running`
* `progress_percent` stamped with a number nothing measured
* an artifact list that is always `[]` but reads as "produced no files"
* a start refusal (attempt ceiling) overridden by the runner
* a poller that hits its ceiling adopting the run as finished

Each is asserted directly. The `SubagentExecutor` and `SubagentResult` seams are
faked at the boundary — that is the correct seam to fake, because the property
under test is what the runner does with a real terminal status, not how LangGraph
executes a graph.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

# `alpha.tools` must load before `alpha.subagents.executor` (authz -> guardrails
# -> tools -> builtins -> self_improvement_tool -> subagents is a real cycle).
import alpha.tools  # noqa: F401
from alpha.subagents import lifecycle_runner
from alpha.subagents.lifecycle import SubagentDeliverable, get_subagent_lifecycle_manager


class _StatusValue:
    """One status. Mirrors the real enum's two members the runner reads:
    ``.value`` and the ``.is_terminal`` property."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    @property
    def is_terminal(self) -> bool:
        return self.value in {"completed", "failed", "cancelled", "timed_out"}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _StatusValue):
            return self.value == other.value
        return self.value == other

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return self.value


class Status:
    """A local stand-in for ``SubagentStatus``.

    ``tests/conftest.py`` injects a ``MagicMock`` for ``alpha.subagents.executor``
    **by design**, to break a circular import that exists in production code (the
    chain is spelled out in that conftest). So the real enum is genuinely
    unavailable here and importing it yields a mock with no ``.FAILED``.

    The runner imports ``SubagentStatus`` from whichever module is in
    ``sys.modules`` at call time, and every test below installs a fake module
    carrying this class. That is the correct seam: what is under test is how the
    runner reacts to a terminal status, not which enum the executor defines.
    """

    PENDING = _StatusValue("pending")
    RUNNING = _StatusValue("running")
    COMPLETED = _StatusValue("completed")
    FAILED = _StatusValue("failed")
    CANCELLED = _StatusValue("cancelled")
    TIMED_OUT = _StatusValue("timed_out")


@pytest.fixture(autouse=True)
def _isolated_lifecycle(tmp_path, monkeypatch):
    """A lifecycle manager whose records live in a throwaway directory."""
    manager = get_subagent_lifecycle_manager(storage_dir=tmp_path / "lifecycle")
    monkeypatch.setattr(
        "alpha.subagents.lifecycle_runner.get_subagent_lifecycle_manager",
        lambda *a, **k: manager,
        raising=False,
    )
    return manager


def _contract(objective: str = "audit the catalog", *, max_attempts: int = 3):
    from alpha.subagents.lifecycle import SubagentContract

    return SubagentContract(
        objective=objective,
        role="general-purpose",
        timeout_seconds=120,
        lease_duration_seconds=60,
        max_attempts=max_attempts,
    )


def _spawn(manager, objective: str = "audit the catalog"):
    return manager.spawn_subagent(parent_agent_id="test", contract=_contract(objective))


class _FakeResult:
    """The minimum surface the runner reads off a terminal execution."""

    def __init__(self, status: _StatusValue, *, text: str = "", error: str = "", stop_reason: str | None = None):
        self.status = status
        self.result = text
        self.error = error
        self.stop_reason = stop_reason


def _install_fake_executor(monkeypatch, result: _FakeResult, *, raise_on_submit: Exception | None = None):
    """Replace the executor + registry with a scripted terminal outcome."""
    seen: dict[str, Any] = {"task": None, "agent": None, "submission_calls": 0}

    class _FakeExecutor:
        def __init__(self, *, config, tools, app_config=None, thread_id=None, user_id=None, trace_id=None):
            seen["agent"] = config.name
            seen["tools"] = tools

        def execute_async(self, task: str, task_id: str | None = None) -> str:
            seen["submission_calls"] += 1
            seen["task"] = task
            if raise_on_submit is not None:
                raise raise_on_submit
            return "exec-test"

    fake_module = types.ModuleType("alpha.subagents.executor")
    fake_module.SubagentExecutor = _FakeExecutor
    fake_module.SubagentStatus = Status
    fake_module.cleanup_background_task = lambda execution_id: seen.setdefault("cleaned", execution_id)
    fake_module.get_background_task_result = lambda execution_id: result
    monkeypatch.setitem(sys.modules, "alpha.subagents.executor", fake_module)

    # The lazy package root resolves names through __getattr__, so both paths need
    # the fake or the runner would reach the real module.
    monkeypatch.setattr("alpha.subagents.executor", fake_module, raising=False)
    return seen


# ---------------------------------------------------------------------------
# The happy path: a record that actually runs and completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_completed_execution_marks_the_record_completed(_isolated_lifecycle, monkeypatch):
    rec = _spawn(_isolated_lifecycle)
    seen = _install_fake_executor(monkeypatch, _FakeResult(Status.COMPLETED, text="I checked 8 definitions."))

    ok = await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="general-purpose", task="audit the catalog", heartbeat_seconds=0.01, poll_seconds=0.01)

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert ok is True
    assert after.status.value == "completed", "a completed execution must complete the record"
    assert after.started_at is not None, "started_at is the whole point of the runner"
    assert after.result.status == "completed"
    assert "8 definitions" in after.result.summary, "the summary must be the subagent's own words"
    assert seen["submission_calls"] == 1, "the executor must actually have been driven"


@pytest.mark.asyncio
async def test_the_task_reaches_the_executor_and_not_just_the_lifecycle(_isolated_lifecycle, monkeypatch):
    """A record that transitions without a real submission is the old bug."""
    rec = _spawn(_isolated_lifecycle, objective="count the routes")
    seen = _install_fake_executor(monkeypatch, _FakeResult(Status.COMPLETED, text="done"))

    await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="bash", task="count the routes", heartbeat_seconds=0.01, poll_seconds=0.01)

    assert seen["task"] == "count the routes"
    assert seen["agent"] == "bash", "the requested definition must be the one executed"


# ---------------------------------------------------------------------------
# The honesty boundaries: a failure is never dressed as a success
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,error",
    [
        (Status.FAILED, "the model call failed"),
        (Status.CANCELLED, "cancelled by the operator"),
        (Status.TIMED_OUT, "exceeded 1800s"),
    ],
)
@pytest.mark.asyncio
async def test_every_non_completed_terminal_status_fails_with_its_real_reason(_isolated_lifecycle, monkeypatch, status, error):
    rec = _spawn(_isolated_lifecycle)
    _install_fake_executor(monkeypatch, _FakeResult(status, text="partial output that must not count", error=error))

    ok = await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="general-purpose", task="x", heartbeat_seconds=0.01, poll_seconds=0.01)

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert ok is False
    assert after.status.value == "failed", f"{status} must not be recorded as completed"
    assert error in after.result.summary, "the real reason must survive to the record"
    assert "partial output" not in after.result.summary, "a failed run's prose is not a deliverable"


@pytest.mark.asyncio
async def test_a_submission_exception_fails_the_record_rather_than_leaving_it_running(_isolated_lifecycle, monkeypatch):
    rec = _spawn(_isolated_lifecycle)
    _install_fake_executor(monkeypatch, _FakeResult(Status.COMPLETED), raise_on_submit=RuntimeError("no sandbox available"))

    ok = await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="general-purpose", task="x", heartbeat_seconds=0.01, poll_seconds=0.01)

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert ok is False
    assert after.status.value == "failed", "a record must never be left `running` because the runner died"
    assert "no sandbox available" in after.result.summary


@pytest.mark.asyncio
async def test_an_unknown_definition_fails_with_the_name_rather_than_registering_forever(_isolated_lifecycle, monkeypatch):
    rec = _spawn(_isolated_lifecycle)
    _install_fake_executor(monkeypatch, _FakeResult(Status.COMPLETED, text="never"))

    ok = await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="no-such-agent", task="x", heartbeat_seconds=0.01, poll_seconds=0.01)

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert ok is False
    assert after.status.value == "failed"
    assert "no-such-agent" in after.result.summary, "the refusal must name what it could not find"


@pytest.mark.asyncio
async def test_a_poller_ceiling_is_a_timeout_not_a_completion(_isolated_lifecycle, monkeypatch):
    """A run that never reports terminal must not be adopted as finished."""
    rec = _spawn(_isolated_lifecycle)

    class _NeverTerminal:
        status = Status.RUNNING

    _install_fake_executor(monkeypatch, _NeverTerminal())

    ok = await lifecycle_runner.run_registered_subagent(
        rec.subagent_id,
        agent_name="general-purpose",
        task="x",
        heartbeat_seconds=0.01,
        poll_seconds=0.01,
        wait_ceiling_seconds=0.05,
    )

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert ok is False
    assert after.status.value == "failed"
    assert "abandoned" in after.result.summary.lower()


# ---------------------------------------------------------------------------
# Attempt accounting and lease liveness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_start_refusal_is_honoured_and_nothing_is_executed(_isolated_lifecycle, monkeypatch):
    """`start_subagent` returns False at the attempt ceiling; the runner must stop."""
    rec = _spawn(_isolated_lifecycle)
    # `max_attempts` is a read-only property derived from the contract, and the
    # contract field is clamped to a minimum of 1, so the ceiling is exhausted
    # through the settable `attempt` counter instead: `attempts_exhausted` is
    # `attempt >= max_attempts`.
    _isolated_lifecycle._records[rec.subagent_id].attempt = 99

    seen = _install_fake_executor(monkeypatch, _FakeResult(Status.COMPLETED, text="must not run"))

    ok = await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="general-purpose", task="x", heartbeat_seconds=0.01, poll_seconds=0.01)

    assert ok is False
    assert seen["submission_calls"] == 0, "a refused start must not reach the executor"


@pytest.mark.asyncio
async def test_the_lease_is_renewed_so_a_live_worker_is_not_called_stalled(_isolated_lifecycle, monkeypatch):
    """The exact fabricated measurement 3.T2 warns about."""
    rec = _spawn(_isolated_lifecycle)
    before = _isolated_lifecycle.get_subagent(rec.subagent_id).lease.renew_count

    class _SlowTerminal:
        """Terminal only after several polls, so heartbeats must fire."""

        status = Status.RUNNING
        polls = 0

        def __init__(self):
            self.status = Status.RUNNING

    fake = _SlowTerminal()

    class _Executor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, task, task_id=None):
            return "exec-slow"

    module = types.ModuleType("alpha.subagents.executor")
    module.SubagentExecutor = _Executor
    module.SubagentStatus = Status
    module.cleanup_background_task = lambda e: None

    def _poll(_execution_id):
        fake.polls += 1
        if fake.polls >= 4:
            fake.status = Status.COMPLETED
            fake.result = "finished"
            fake.error = ""
            fake.stop_reason = None
        return fake

    module.get_background_task_result = _poll
    monkeypatch.setitem(sys.modules, "alpha.subagents.executor", module)
    monkeypatch.setattr("alpha.subagents.executor", module, raising=False)

    await lifecycle_runner.run_registered_subagent(
        rec.subagent_id,
        agent_name="general-purpose",
        task="x",
        heartbeat_seconds=0.0,
        poll_seconds=0.01,
        wait_ceiling_seconds=5.0,
    )

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert after.lease.renew_count > before, "a healthy worker must keep its lease alive"
    assert after.status.value == "completed"


@pytest.mark.asyncio
async def test_no_progress_percentage_is_stamped_because_nothing_measures_one(_isolated_lifecycle, monkeypatch):
    """`progress_percent` defaults to 0.0 and clamps with max(0.0, v).

    A 0 would read as *measured zero progress*; passing None raises TypeError.
    So the runner renews the lease and records a real status string instead, and
    this asserts the heartbeat carries no percentage at all.
    """
    rec = _spawn(_isolated_lifecycle)
    stamped: list[dict[str, Any]] = []

    class _Executor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, task, task_id=None):
            return "exec-hb"

    module = types.ModuleType("alpha.subagents.executor")
    module.SubagentExecutor = _Executor
    module.SubagentStatus = Status
    module.cleanup_background_task = lambda e: None

    state = {"n": 0}

    def _poll(_e):
        state["n"] += 1
        fake = _FakeResult(Status.RUNNING if state["n"] < 3 else Status.COMPLETED, text="done")
        return fake

    module.get_background_task_result = _poll
    monkeypatch.setitem(sys.modules, "alpha.subagents.executor", module)
    monkeypatch.setattr("alpha.subagents.executor", module, raising=False)

    original = _isolated_lifecycle.record_heartbeat

    def _spy(subagent_id, *args, **kwargs):
        stamped.append(kwargs)
        return original(subagent_id, *args, **kwargs)

    monkeypatch.setattr(_isolated_lifecycle, "record_heartbeat", _spy)

    await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="general-purpose", task="x", heartbeat_seconds=0.0, poll_seconds=0.01)

    assert stamped == [], "record_heartbeat defaults progress_percent to 0.0, which nothing here measures"


# ---------------------------------------------------------------------------
# Capped runs keep their reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_capped_run_completes_but_carries_its_stop_reason(_isolated_lifecycle, monkeypatch):
    rec = _spawn(_isolated_lifecycle)
    _install_fake_executor(
        monkeypatch,
        _FakeResult(Status.COMPLETED, text="partial work", stop_reason="token_capped"),
    )

    await lifecycle_runner.run_registered_subagent(rec.subagent_id, agent_name="general-purpose", task="x", heartbeat_seconds=0.01, poll_seconds=0.01)

    after = _isolated_lifecycle.get_subagent(rec.subagent_id)
    assert after.status.value == "completed", "a capped run with usable output still completed"
    assert any("token_capped" in r for r in after.result.recommendations), "the lead must be able to tell finished from capped"


def test_an_empty_completion_is_reported_as_empty_not_invented(monkeypatch):
    """A deliverable must never acquire a reassuring sentence nobody produced."""
    # `_record_terminal` imports SubagentStatus from sys.modules, and conftest
    # mocks that module, so the fake has to be installed here too.
    module = types.ModuleType("alpha.subagents.executor")
    module.SubagentStatus = Status
    monkeypatch.setitem(sys.modules, "alpha.subagents.executor", module)
    monkeypatch.setattr("alpha.subagents.executor", module, raising=False)

    calls: list[SubagentDeliverable] = []

    class _Manager:
        def complete_subagent(self, _id, deliverable):
            calls.append(deliverable)
            return True

        def fail_subagent(self, *_a, **_k):
            raise AssertionError("an empty completion is still a completion")

    lifecycle_runner._record_terminal(_Manager(), "sub-x", _FakeResult(Status.COMPLETED, text="   "))

    assert len(calls) == 1
    assert "without returning any text" in calls[0].summary
    assert calls[0].artifacts == [], "no artifact list is measured, so none is claimed"
    assert asyncio.iscoroutinefunction(lifecycle_runner.run_registered_subagent)
