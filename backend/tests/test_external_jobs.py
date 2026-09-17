"""Comprehensive tests for the Decoupled External Job Engine."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_workspace.jobs import (
    ExternalJobRunner,
    JobPriority,
    JobSpec,
    JobStatus,
    PersistentJobQueue,
)
from agent_workspace.tools.builtins.job_tool import job_tool


@pytest.fixture(autouse=True)
def host_job_config(monkeypatch):
    monkeypatch.setattr("agent_workspace.jobs.runner.get_app_config", lambda: SimpleNamespace(sandbox=SimpleNamespace(allow_host_bash=True)))


def test_job_queue_priority_ordering():
    queue = PersistentJobQueue(max_concurrency=2)
    spec_normal = JobSpec(title="Normal Task", command=["echo", "normal"], priority=JobPriority.NORMAL)
    spec_critical = JobSpec(title="Critical Task", command=["echo", "critical"], priority=JobPriority.CRITICAL)
    spec_low = JobSpec(title="Low Task", command=["echo", "low"], priority=JobPriority.LOW)
    spec_high = JobSpec(title="High Task", command=["echo", "high"], priority=JobPriority.HIGH)

    queue.enqueue(spec_normal)
    queue.enqueue(spec_critical)
    queue.enqueue(spec_low)
    queue.enqueue(spec_high)

    # Dequeue order should strictly follow priority: CRITICAL -> HIGH -> NORMAL -> LOW
    first = queue.dequeue_next()
    assert first is not None
    assert first.job_id == spec_critical.job_id

    second = queue.dequeue_next()
    assert second is not None
    assert second.job_id == spec_high.job_id


def run_process_test(awaitable):
    factory = asyncio.ProactorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(awaitable)


def test_job_runner_successful_execution():
    runner = ExternalJobRunner()
    # Cross-platform python command to echo hello
    cmd = [sys.executable, "-c", "print('hello from external job')"]
    spec = JobSpec(title="Echo Test", command=cmd)

    result = run_process_test(runner.submit_and_wait(spec, authorized_operator=True))
    assert result.status == JobStatus.COMPLETED
    assert result.exit_code == 0
    assert "hello from external job" in result.stdout
    assert result.execution_seconds >= 0.0


def test_job_runner_timeout():
    runner = ExternalJobRunner()
    # Command that sleeps longer than timeout
    cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
    spec = JobSpec(title="Sleep Test", command=cmd)
    spec.resources.timeout_seconds = 0.5

    result = run_process_test(runner.submit_and_wait(spec, authorized_operator=True))
    assert result.status == JobStatus.TIMED_OUT
    assert "timed out" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_job_runner_cancellation():
    queue = PersistentJobQueue()
    runner = ExternalJobRunner(queue=queue)
    cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
    spec = JobSpec(title="Cancel Test", command=cmd)

    job_id = await runner.submit_async(spec, authorized_operator=True)
    assert runner.cancel(job_id) is True

    # Allow cancellation to propagate
    await asyncio.sleep(0.1)
    status = queue.get_status(job_id)
    assert status is not None
    assert status.status == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_job_tool_cannot_bypass_operator_gate(monkeypatch):
    import importlib
    import json

    module = importlib.import_module("agent_workspace.tools.builtins.job_tool")
    queue = PersistentJobQueue()
    runner = ExternalJobRunner(queue)
    monkeypatch.setattr(module, "_GLOBAL_QUEUE", queue)
    monkeypatch.setattr(module, "_GLOBAL_RUNNER", runner)
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    finished = asyncio.Event()
    queue.add_listener(lambda job_id, status: finished.set() if status == JobStatus.FAILED else None)
    submitted = json.loads(job_tool.invoke({"action": "submit", "command": "must-not-run"}))
    await asyncio.wait_for(finished.wait(), timeout=5)
    assert queue.get_status(submitted["job_id"]).status == JobStatus.FAILED
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_denies_host_execution_by_default(monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runner = ExternalJobRunner()
    spec = JobSpec(command=["must-not-run"])
    for submit in (runner.submit_async, runner.submit_and_wait):
        with pytest.raises(PermissionError):
            await submit(spec)
    assert runner.queue.list_jobs() == []
    result = await runner.execute_spec(spec)
    assert result.status == JobStatus.FAILED
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_job_cancellation_drains_process(monkeypatch):
    started = asyncio.Event()
    draining = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def communicate():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()
        draining.set()
        await release.wait()
        return b"", b""

    proc = SimpleNamespace(communicate=AsyncMock(side_effect=communicate), kill=Mock(), returncode=None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    runner = ExternalJobRunner()
    spec = JobSpec(command=["test-process"])
    job_id = await runner.submit_async(spec, authorized_operator=True)
    task = runner._running_tasks[job_id]
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert runner.cancel(job_id)
        await asyncio.wait_for(draining.wait(), timeout=5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        proc.kill.assert_called_once()
        assert calls == 2
        assert runner.queue.get_status(job_id).status == JobStatus.CANCELLED
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_runner_bounds_captured_output():
    runner = ExternalJobRunner()
    spec = JobSpec(command=[sys.executable, "-c", "import sys; sys.stdout.write('x' * 500000); sys.stderr.write('y' * 500000)"])
    spec.resources.timeout_seconds = 60
    result = run_process_test(runner.submit_and_wait(spec, authorized_operator=True))
    assert result.status == JobStatus.COMPLETED
    assert len(result.stdout) == 200000
    assert len(result.stderr) == 200000


def test_runner_filters_inherited_environment(monkeypatch):
    monkeypatch.setenv("JOBS_TEST_SECRET", "must-not-inherit")
    monkeypatch.setenv("JOBS_TEST_BENIGN", "preserved")
    runner = ExternalJobRunner()
    spec = JobSpec(
        command=[sys.executable, "-c", "import os; print(os.getenv('JOBS_TEST_SECRET')); print(os.environ['JOBS_TEST_BENIGN']); print(os.environ['JOBS_INJECTED_TOKEN'])"],
        env={"JOBS_INJECTED_TOKEN": "explicit"},
    )
    result = run_process_test(runner.submit_and_wait(spec, authorized_operator=True))
    assert result.status == JobStatus.COMPLETED
    assert result.stdout.splitlines() == ["None", "preserved", "explicit"]
