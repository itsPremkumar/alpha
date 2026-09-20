import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from alpha.jobs import ExternalJobRunner, JobPriority, JobResult, JobSpec, JobStatus, PersistentJobQueue


@pytest.fixture
def host_config(monkeypatch):
    config = SimpleNamespace(sandbox=SimpleNamespace(allow_host_bash=True))
    monkeypatch.setattr("alpha.jobs.runner.get_app_config", lambda: config)
    return config


class ControlledProcess:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.draining = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.returncode = 0
        self.kill = Mock()
        self.wait = AsyncMock(return_value=0)
        self.calls = 0

    async def communicate(self):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        else:
            self.draining.set()
            await self.cleanup_release.wait()
        return b"done", b""


@pytest.fixture
def processes(monkeypatch, host_config):
    registry = {}
    order = []

    async def spawn(name, **kwargs):
        order.append(name)
        return registry[name]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return registry, order


async def wait_event(event):
    await asyncio.wait_for(event.wait(), timeout=5)


async def loop_checkpoint():
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await wait_event(event)


async def wait_terminal(queue, job_id):
    finished = asyncio.Event()
    terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.CANCELLED}

    def on_change(changed_id, status):
        if changed_id == job_id and status in terminal:
            finished.set()

    queue.add_listener(on_change)
    try:
        if queue.get_status(job_id).status not in terminal:
            await wait_event(finished)
        return queue.get_status(job_id)
    finally:
        queue.remove_listener(on_change)


async def drain(runner, registry):
    for process in registry.values():
        process.release.set()
        process.cleanup_release.set()
    for result in runner.queue.list_jobs(limit=1000):
        runner.cancel(result.job_id)
    await asyncio.wait_for(asyncio.gather(*runner._running_tasks.values(), return_exceptions=True), timeout=5)
    await loop_checkpoint()


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_concurrency_must_be_a_positive_integer(value):
    with pytest.raises(ValueError, match="positive integer"):
        PersistentJobQueue(max_concurrency=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [1, 3])
async def test_bounded_dispatch_priority_and_fifo(processes, capacity):
    registry, order = processes
    queue = PersistentJobQueue(max_concurrency=capacity)
    runner = ExternalJobRunner(queue)
    blockers = [JobSpec(command=[f"blocker-{index}"]) for index in range(capacity)]
    waiting = [
        JobSpec(command=["low"], priority=JobPriority.LOW),
        JobSpec(command=["normal-first"], priority=JobPriority.NORMAL, created_at=100),
        JobSpec(command=["critical"], priority=JobPriority.CRITICAL),
        JobSpec(command=["high"], priority=JobPriority.HIGH),
        JobSpec(command=["normal-second"], priority=JobPriority.NORMAL, created_at=0),
    ]
    for spec in blockers + waiting:
        registry[spec.command[0]] = ControlledProcess()
    try:
        for spec in blockers:
            await runner.submit_async(spec, authorized_operator=True)
        for spec in blockers:
            await wait_event(registry[spec.command[0]].started)
        for spec in waiting:
            await runner.submit_async(spec, authorized_operator=True)
        await loop_checkpoint()
        assert len(runner._running_tasks) == capacity
        assert len(order) == capacity
        assert all(queue.get_status(spec.job_id).status == JobStatus.QUEUED for spec in waiting)
        assert all(queue.get_status(spec.job_id).started_at is None for spec in waiting)
        registry[blockers[0].command[0]].release.set()
        for name in ["critical", "high", "normal-first", "normal-second", "low"]:
            await wait_event(registry[name].started)
            assert order[-1] == name
            assert len(queue.list_jobs(status=JobStatus.RUNNING)) <= capacity
            registry[name].release.set()
        for spec in blockers[1:]:
            registry[spec.command[0]].release.set()
        for spec in blockers + waiting:
            result = await wait_terminal(queue, spec.job_id)
            assert result.status == JobStatus.COMPLETED
            assert result.started_at is not None
        await loop_checkpoint()
        assert not runner._running_tasks
        assert queue.dequeue_next() is None
    finally:
        await drain(runner, registry)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_through_queue", [False, True])
async def test_queued_cancellation_never_executes(processes, cancel_through_queue):
    registry, order = processes
    runner = ExternalJobRunner(PersistentJobQueue(max_concurrency=1))
    blocker = JobSpec(command=["blocker"])
    cancelled = JobSpec(command=["cancelled"], priority=JobPriority.CRITICAL)
    next_job = JobSpec(command=["next"])
    for spec in [blocker, cancelled, next_job]:
        registry[spec.command[0]] = ControlledProcess()
    try:
        await runner.submit_async(blocker, authorized_operator=True)
        await wait_event(registry["blocker"].started)
        await runner.submit_async(cancelled, authorized_operator=True)
        await runner.submit_async(next_job, authorized_operator=True)
        cancel = runner.queue.cancel_job if cancel_through_queue else runner.cancel
        assert cancel(cancelled.job_id)
        assert not cancel(cancelled.job_id)
        registry["blocker"].release.set()
        await wait_event(registry["next"].started)
        assert order == ["blocker", "next"]
        result = runner.queue.get_status(cancelled.job_id)
        assert result.status == JobStatus.CANCELLED
        assert result.started_at is None
        assert result.completed_at is not None
    finally:
        await drain(runner, registry)


@pytest.mark.asyncio
async def test_running_cancellation_holds_slot_until_cleanup_finishes(processes):
    registry, order = processes
    queue = PersistentJobQueue(max_concurrency=1)
    runner = ExternalJobRunner(queue)
    first = JobSpec(command=["first"])
    second = JobSpec(command=["second"])
    registry.update(first=ControlledProcess(), second=ControlledProcess())
    try:
        await runner.submit_async(first, authorized_operator=True)
        await wait_event(registry["first"].started)
        await runner.submit_async(second, authorized_operator=True)
        task = runner._running_tasks[first.job_id]
        assert runner.cancel(first.job_id)
        await wait_event(registry["first"].draining)
        assert queue.dequeue_next() is None
        assert queue.get_status(second.job_id).status == JobStatus.QUEUED
        assert order == ["first"]
        task.cancel()
        await loop_checkpoint()
        assert order == ["first"]
        registry["first"].cleanup_release.set()
        await wait_event(registry["second"].started)
        assert task.cancelled()
        assert queue.get_status(first.job_id).status == JobStatus.CANCELLED
        registry["first"].kill.assert_called_once()
    finally:
        await drain(runner, registry)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["submit_and_wait", "execute_spec"])
async def test_waiting_entrypoints_share_capacity_and_can_be_cancelled(processes, entrypoint):
    registry, order = processes
    runner = ExternalJobRunner(PersistentJobQueue(max_concurrency=1))
    first = JobSpec(command=["first"])
    second = JobSpec(command=["second"])
    registry.update(first=ControlledProcess(), second=ControlledProcess())
    waiter = None
    try:
        await runner.submit_async(first, authorized_operator=True)
        await wait_event(registry["first"].started)
        waiter = asyncio.create_task(getattr(runner, entrypoint)(second, authorized_operator=True))
        await loop_checkpoint()
        assert runner.queue.get_status(second.job_id).status == JobStatus.QUEUED
        assert order == ["first"]
        assert runner.cancel(second.job_id)
        result = await asyncio.wait_for(waiter, timeout=5)
        assert result.status == JobStatus.CANCELLED
        assert order == ["first"]
    finally:
        await drain(runner, registry)
        if waiter is not None:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_host_gate_rechecked_after_wait_and_spec_is_snapshotted(processes, host_config):
    registry, order = processes
    runner = ExternalJobRunner(PersistentJobQueue(max_concurrency=1))
    first = JobSpec(command=["first"], owner_id="alice")
    second = JobSpec(command=["second"], owner_id="bob")
    registry.update(first=ControlledProcess(), second=ControlledProcess())
    try:
        await runner.submit_async(first, authorized_operator=True)
        await wait_event(registry["first"].started)
        await runner.submit_async(second, authorized_operator=True)
        second.owner_id = "alice"
        second.command = ["mutated"]
        assert runner.queue.get_status(second.job_id, owner_id="alice") is None
        assert runner.queue.get_spec(second.job_id).command == ["second"]
        host_config.sandbox.allow_host_bash = False
        registry["first"].release.set()
        result = await wait_terminal(runner.queue, second.job_id)
        assert result.status == JobStatus.FAILED
        assert "allow_host_bash" in result.error
        assert order == ["first"]
        assert runner.queue.get_status(second.job_id, owner_id="bob") is not None
    finally:
        await drain(runner, registry)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["launch", "exit", "timeout", "environment"])
async def test_failures_release_capacity_and_worker_can_restart(processes, monkeypatch, failure):
    registry, order = processes
    runner = ExternalJobRunner(PersistentJobQueue(max_concurrency=1))
    first = JobSpec(command=["first"])
    second = JobSpec(command=["second"])
    registry["second"] = ControlledProcess()
    if failure == "exit":
        registry["first"] = ControlledProcess()
        registry["first"].returncode = 1
        registry["first"].release.set()
    elif failure == "timeout":
        registry["first"] = ControlledProcess()
        registry["first"].communicate = AsyncMock(side_effect=TimeoutError)
    elif failure == "environment":
        monkeypatch.setattr("alpha.jobs.runner.build_sandbox_env", Mock(side_effect=[RuntimeError("bad environment"), {}, {}]))
    try:
        await runner.submit_async(first, authorized_operator=True)
        await runner.submit_async(second, authorized_operator=True)
        await wait_event(registry["second"].started)
        expected = JobStatus.TIMED_OUT if failure == "timeout" else JobStatus.FAILED
        assert runner.queue.get_status(first.job_id).status == expected
        registry["second"].release.set()
        assert (await wait_terminal(runner.queue, second.job_id)).status == JobStatus.COMPLETED
        await loop_checkpoint()
        assert not runner._running_tasks
        third = JobSpec(command=["third"])
        registry["third"] = ControlledProcess()
        registry["third"].release.set()
        result = await asyncio.wait_for(runner.submit_and_wait(third, authorized_operator=True), timeout=5)
        assert result.status == JobStatus.COMPLETED
    finally:
        await drain(runner, registry)


@pytest.mark.asyncio
async def test_cancelling_waiter_removes_queued_job(processes):
    registry, order = processes
    runner = ExternalJobRunner(PersistentJobQueue(max_concurrency=1))
    first = JobSpec(command=["first"])
    second = JobSpec(command=["second"])
    registry.update(first=ControlledProcess(), second=ControlledProcess())
    waiter = None
    try:
        await runner.submit_async(first, authorized_operator=True)
        await wait_event(registry["first"].started)
        waiter = asyncio.create_task(runner.submit_and_wait(second, authorized_operator=True))
        await loop_checkpoint()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, timeout=5)
        assert runner.queue.get_status(second.job_id).status == JobStatus.CANCELLED
        registry["first"].release.set()
        await wait_terminal(runner.queue, first.job_id)
        await loop_checkpoint()
        assert order == ["first"]
        assert not runner._pending_ids
        assert not runner._completions
    finally:
        await drain(runner, registry)
        if waiter is not None:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_unapproved_execution_cannot_release_an_active_slot(processes):
    registry, order = processes
    runner = ExternalJobRunner(PersistentJobQueue(max_concurrency=1))
    first = JobSpec(command=["first"])
    second = JobSpec(command=["second"])
    registry.update(first=ControlledProcess(), second=ControlledProcess())
    try:
        await runner.submit_async(first, authorized_operator=True)
        await wait_event(registry["first"].started)
        await runner.submit_async(second, authorized_operator=True)
        denied = await runner.execute_spec(first)
        assert denied.status == JobStatus.FAILED
        assert runner.queue.get_status(first.job_id).status == JobStatus.RUNNING
        assert runner.queue.dequeue_next() is None
        assert order == ["first"]
    finally:
        await drain(runner, registry)


def test_running_listener_can_read_and_cancel_without_deadlock():
    queue = PersistentJobQueue(max_concurrency=1)
    spec = JobSpec(command=["unused"])
    finished = threading.Event()
    observed = []

    def listener(job_id, status):
        observed.append(queue.get_status(job_id).status)
        if status == JobStatus.RUNNING:
            queue.cancel_job(job_id)

    queue.add_listener(listener)
    queue.enqueue(spec)

    def claim():
        queue.dequeue_next()
        finished.set()

    worker = threading.Thread(target=claim, daemon=True)
    worker.start()
    assert finished.wait(timeout=5)
    worker.join(timeout=5)
    assert observed == [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLED]


def test_queue_claims_are_atomic_and_cancelled_running_slots_are_retained():
    queue = PersistentJobQueue(max_concurrency=2)
    specs = [JobSpec(command=["unused"]) for _ in range(8)]
    for spec in specs:
        queue.enqueue(spec)
    start = threading.Event()

    def claim():
        assert start.wait(timeout=5)
        return queue.dequeue_next()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(claim) for _ in specs]
        start.set()
        claimed = [result for future in futures if (result := future.result(timeout=5)) is not None]
    assert len(claimed) == 2
    assert len({spec.job_id for spec in claimed}) == 2
    assert queue.cancel_job(claimed[0].job_id)
    assert queue.dequeue_next() is None
    queue.complete_job(JobResult(job_id=claimed[0].job_id, status=JobStatus.COMPLETED))
    assert queue.get_status(claimed[0].job_id).status == JobStatus.CANCELLED
    assert queue.dequeue_next() is not None
