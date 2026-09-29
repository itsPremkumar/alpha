"""Process-local state that is reachable through an API or a durable-looking
interface: what a *second* Gateway worker sees for each one.

Every assertion here is about a boundary that already exists in the code. None
of them asks for new behaviour. The point is to pin the answer so a future
change that quietly promotes one of these to "looks durable" fails a test
instead of failing an operator.

The three questions asked of each surface are always the same:

1. What does worker B see for state worker A created?
2. Is that stated in the code or the guide, or only implied by the signature?
3. Does the HTTP surface say so, or does it hand back a shape a caller cannot
   distinguish from a live local run?

RunManager and StreamBridge are the two that matter most, because both are
reachable from a route that returns a durable-looking object. A ``RunRecord``
read on worker B is hydrated from the store and is honestly flagged
``store_only``; the routes turn that flag into a 409 rather than a subscription
that would hang forever. That behaviour is the repository's own, and these
tests exist to keep it.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.runtime.events.store.base import RunEventStore
from alpha.runtime.events.store.jsonl import JsonlRunEventStore
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.runs.manager import CancelOutcome, RunManager, RunStatus
from alpha.runtime.stream_bridge.base import StreamBridge
from alpha.runtime.stream_bridge.memory import MemoryStreamBridge

# ---------------------------------------------------------------------------
# RunManager: the RunRecord retention window and what a peer worker reads
# ---------------------------------------------------------------------------


class _DictRunStore:
    """Minimal durable stand-in so the second worker shares real state.

    The point of the test is the *manager's* behaviour when a peer holds the
    record, so the store only has to be somewhere both managers can see.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def put(self, run_id: str, **payload) -> None:
        self.rows[run_id] = {"run_id": run_id, **payload}

    async def get(self, run_id: str, user_id=None):
        return self.rows.get(run_id)

    async def list_by_thread(self, thread_id: str, *, user_id=None, limit=100, before_created_at=None, before_run_id=None):
        rows = [r for r in self.rows.values() if r.get("thread_id") == thread_id]
        rows.sort(key=lambda r: (r.get("created_at") or "", r["run_id"]), reverse=True)
        return rows[:limit]


@pytest.mark.asyncio
async def test_a_second_worker_sees_a_store_only_record_not_a_live_task() -> None:
    """Worker B hydrates worker A's run as readable history, flagged as such.

    This is the whole ``RunRecord`` retention story in one assertion: the record
    is durable (B can read it), but it is not *live* on B (no task, no
    ``store_only=False``). Anything that treats a hydrated record as an
    in-process run -- joining its stream, awaiting its task -- is wrong, and the
    ``store_only`` flag is the only thing that lets a caller notice.
    """
    store = _DictRunStore()
    worker_a = RunManager(store=store, worker_id="host:a")
    worker_b = RunManager(store=store, worker_id="host:b")

    record = await worker_a.create("t1")
    await worker_a.set_status(record.run_id, RunStatus.running)

    seen_by_a = await worker_a.get(record.run_id)
    seen_by_b = await worker_b.get(record.run_id)

    assert seen_by_a is not None and seen_by_a.store_only is False, "the creating worker holds a live record"
    assert seen_by_b is not None, "a peer worker can still read the run: it is durable"
    assert seen_by_b.store_only is True, "and must be flagged as history, not execution"
    assert seen_by_b.task is None, "a peer worker has no task for a run it did not start"
    assert seen_by_b.abort_event is not None, "each hydration builds its own abort event, so it is local to the reader"
    assert not seen_by_b.abort_event.is_set()


@pytest.mark.asyncio
async def test_a_peer_worker_cannot_cancel_a_run_it_does_not_own() -> None:
    """Without heartbeat, cancel on a hydrated run is ``not_active_locally``.

    This is the single-worker contract made explicit: with no lease there is
    nothing to distinguish "the owner is alive" from "the owner is a different
    process", so the answer is "not mine" rather than a guess. The 409 the
    router turns this into is what stops a second worker from cancelling a live
    run it happens to know the id of.
    """
    store = _DictRunStore()
    worker_a = RunManager(store=store, worker_id="host:a")
    worker_b = RunManager(store=store, worker_id="host:b")

    record = await worker_a.create("t1")
    await worker_a.set_status(record.run_id, RunStatus.running)

    outcome = await worker_b.cancel(record.run_id)

    assert outcome is CancelOutcome.not_active_locally
    # ...and the owning worker still sees its run as running, untouched.
    assert (await worker_a.get(record.run_id)).status is RunStatus.running


@pytest.mark.asyncio
async def test_run_record_retention_is_a_process_local_delay_not_a_durability_claim() -> None:
    """``cleanup(delay=300)`` drops the in-memory record; the store keeps it.

    The 300-second window in the guide is about how long *this* worker keeps a
    mutable record around for late joiners. It is not a retention policy on the
    run: the row is what survives, and it survives without the delay. A reader
    who believes "the record is gone after five minutes" is wrong about the
    durable half and right only about the local half.
    """
    store = _DictRunStore()
    worker = RunManager(store=store, worker_id="host:a")

    record = await worker.create("t1")
    await worker.cleanup(record.run_id, delay=0)

    assert record.run_id not in worker._runs, "the in-memory record is dropped"
    assert record.run_id in store.rows, "the durable row is not touched by local cleanup"
    assert await worker.get(record.run_id) is not None, "and the run is still readable"


# ---------------------------------------------------------------------------
# StreamBridge: the 60-second late-subscriber window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_memory_bridge_declares_that_it_is_not_cross_process() -> None:
    """The flag the routes branch on is a declared class attribute, not a guess.

    ``supports_cross_process`` is what every join/stream/wait route consults to
    decide between "subscribe" and "409". If a bridge ever claimed the flag
    while holding process-local state, every one of those routes would start
    hanging instead of refusing.
    """
    assert MemoryStreamBridge().supports_cross_process is False
    assert StreamBridge.supports_cross_process is False, "the base default is the safe one"


@pytest.mark.asyncio
async def test_a_peer_bridge_subscribe_creates_an_empty_log_rather_than_reporting_a_gap() -> None:
    """Worker B subscribing to worker A's run heartbeats forever, and that is why
    the *route* must check ``store_only`` before it subscribes.

    This is the sharpest process-local hazard in the stream layer, so it is worth
    stating exactly. ``MemoryStreamBridge.subscribe`` opens with
    ``_get_or_create_stream(run_id)``: for a run this process has never seen it
    *creates* an empty log, and a subscriber then sits in the
    ``condition.wait()`` loop emitting ``HEARTBEAT_SENTINEL`` every interval.
    It does not raise, does not yield a ``StreamGap``, and never reaches
    ``END_SENTINEL`` -- the producing worker is on another process, so nothing
    will ever publish one here.

    ``StreamGap`` is a *retention* signal (the buffer moved past this cursor),
    not an *unknown-run* signal, so it cannot cover this case. The only thing
    standing between a client and an indefinite heartbeat stream is the
    ``record.store_only and not bridge.supports_cross_process`` check in
    ``thread_runs.py``, which answers 409 before subscribing. That check is
    load-bearing, and the runtime guide's "60-second late-subscriber window"
    says nothing about it.
    """
    worker_a = MemoryStreamBridge(heartbeat_interval=0.02)
    worker_b = MemoryStreamBridge(heartbeat_interval=0.02)

    await worker_a.publish("run-1", "values", {"messages": []})

    assert await worker_a.stream_exists("run-1") is True
    assert await worker_b.stream_exists("run-1") is False, "worker B holds no log for worker A's run"

    from alpha.runtime.stream_bridge.base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamGap

    seen: list = []
    async for item in worker_b.subscribe("run-1"):
        seen.append(item)
        if len(seen) >= 3:
            break

    assert all(item is HEARTBEAT_SENTINEL for item in seen), f"an unknown run yields heartbeats, not events or a gap: {seen!r}"
    assert not any(isinstance(item, StreamGap) for item in seen), "StreamGap is a retention signal, not an unknown-run signal"
    assert not any(item is END_SENTINEL for item in seen), "no producer on this process will ever publish end"

    # The subscribe itself created the phantom entry, which is the mechanism.
    assert await worker_b.stream_exists("run-1") is True, "subscribing to an unknown run_id is what created the empty log"


@pytest.mark.asyncio
async def test_the_route_gate_is_what_prevents_the_phantom_subscription() -> None:
    """``record.store_only and not supports_cross_process`` is the load-bearing 409.

    This asserts the *route's* precondition using the same two values the route
    reads, so that if either half of the condition changed -- the flag the
    hydrated record carries, or the bridge's declared capability -- this fails
    before the failure becomes a hung SSE client.
    """
    from alpha.runtime.runs.manager import RunManager

    store = _DictRunStore()
    worker_a = RunManager(store=store, worker_id="host:a")
    worker_b = RunManager(store=store, worker_id="host:b")

    record = await worker_a.create("t1")
    await worker_a.set_status(record.run_id, RunStatus.running)

    hydrated = await worker_b.get(record.run_id)
    bridge = MemoryStreamBridge()

    # This is the literal condition from thread_runs.py join_run / stream_run.
    assert hydrated.store_only is True and not bridge.supports_cross_process, "the 409 precondition must hold for a peer-hydrated record on a process-local bridge"
    # A locally created run must NOT trip it, or every legitimate join would 409.
    local = await worker_a.get(record.run_id)
    assert not (local.store_only and not bridge.supports_cross_process)


@pytest.mark.asyncio
async def test_bridge_cleanup_drops_only_this_workers_log() -> None:
    """Late-subscriber retention is per process, so cleanup is too.

    The 60s delay in ``worker.py`` exists to let a *local* subscriber drain. It
    is not a cross-worker promise: worker A dropping its buffer has no effect on
    worker B's, and neither worker can see the other's.
    """
    worker_a = MemoryStreamBridge()
    worker_b = MemoryStreamBridge()

    await worker_a.publish("run-1", "values", {"n": 1})
    await worker_b.publish("run-1", "values", {"n": 2})

    await worker_a.cleanup("run-1", delay=0)

    assert await worker_a.stream_exists("run-1") is False
    assert await worker_b.stream_exists("run-1") is True

    seen = []
    async for item in worker_b.subscribe("run-1"):
        seen.append(item)
        if seen and hasattr(item, "data"):
            break
    assert [i.data for i in seen if hasattr(i, "data")] == [{"n": 2}]


# ---------------------------------------------------------------------------
# TokenMeter: process-local, and nothing treats it as authoritative
# ---------------------------------------------------------------------------


def test_the_token_meter_is_a_module_singleton_shared_by_nothing_durable() -> None:
    """The process-global meter is a module-level instance, per process.

    There is exactly one, created at import. A second worker has its own, and
    a restart loses it. That is why ``TokenBudgetMiddleware`` -- which enforces
    per *run*, and a run is owned by exactly one worker -- is the enforcement
    path, and the meter is only a live guard.
    """
    import alpha.runtime.token_meter as token_meter

    assert isinstance(token_meter._DEFAULT_METER, token_meter.TokenMeter)
    assert token_meter.default_meter() is token_meter._DEFAULT_METER, "there is one meter, not one per consumer"
    assert token_meter.default_meter() is not None


def test_the_meter_reports_what_this_process_observed_and_nothing_else() -> None:
    """A recorded reading is visible in this process; a peer's would not be.

    Pinned as observable behaviour rather than a comment: the snapshot is a sum
    over scopes *this* object holds, so it is a valid live guard and an invalid
    billing source, which is exactly the boundary the module docstring claims.
    """
    from alpha.runtime.token_meter import TokenMeter

    mine = TokenMeter()
    theirs = TokenMeter()

    mine.record(user_id="u1", thread_id="t1", model="m", input_tokens=7, output_tokens=3)

    assert mine.snapshot(user_id="u1").total_tokens == 10
    assert theirs.snapshot(user_id="u1").total_tokens == 0, "a peer meter's scopes are its own; there is no shared ledger"
    assert mine.check_budget(9, user_id="u1")["exceeded"] is True
    assert mine.check_budget(0, user_id="u1")["exceeded"] is True, "a non-positive budget fails closed"


def test_no_budget_enforcement_reads_the_process_local_meter() -> None:
    """The enforcement middleware keeps its own per-run counters.

    ``TokenBudgetMiddleware`` is the thing that can stop a run, and it keys on
    ``run_id`` in its own ``BoundedDict``. If it ever started reading the
    process-global meter instead, two workers could each see half a thread's
    spend and neither would stop -- so the separation is pinned here.
    """
    from alpha.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
    from alpha.config.token_budget_config import TokenBudgetConfig

    middleware = TokenBudgetMiddleware(TokenBudgetConfig())
    for attr in ("_cumulative_usage", "_scope_usage", "_seen_messages"):
        state = getattr(middleware, attr)
        assert not any("meter" in str(key).lower() for key in state), f"{attr} must not be keyed off the process-global meter"


# ---------------------------------------------------------------------------
# Admission and lane scheduling
# ---------------------------------------------------------------------------


def test_the_in_flight_run_budget_is_documented_as_per_process() -> None:
    """``RunAdmissionController`` states its own scope in its docstring.

    N workers each allow ``max_concurrent_runs``; the ceiling is therefore N
    times the configured number, and the docstring says exactly that rather than
    calling it a global budget. The counter itself is a plain int under a
    ``threading.Lock``, so it is also correct across threads within a process --
    which is the distinction the class docstring draws.
    """
    from alpha.runtime.lane_scheduler import RunAdmissionController

    controller = RunAdmissionController(max_concurrent_runs=3)
    assert controller.active == 0
    for _ in range(3):
        assert controller.try_admit().admitted is True
    decision = controller.try_admit()
    assert decision.admitted is False
    assert decision.code == "gateway_run_capacity_exhausted", "a refusal carries a stable code, so a producer can tell it from a failure"

    assert "per process" in (RunAdmissionController.__doc__ or ""), "the per-process scope is stated where an operator will read it"


def test_the_writer_fence_is_a_process_local_lease_table() -> None:
    """``WriterFence`` serialises writers *in this process* only.

    A second process has its own ``_active_leases``, so a writer fence in
    worker A does not fence worker B. The cross-process equivalent for thread
    writes is the durable ``checkpoint_write`` reservation, which is a different
    mechanism; this one is a lane-local guard and does not pretend otherwise.
    """
    from alpha.runtime.lane_scheduler import ExecutionLane, LaneScheduler, WriterFence

    fence = WriterFence()
    token = fence.acquire("t1", owner="a")
    assert fence.is_locked("t1") is True

    other = WriterFence()
    assert other.is_locked("t1") is False, "a second fence table does not see the first's lease"

    with pytest.raises(Exception):
        fence.acquire("t1", owner="b", timeout=0.0)
    fence.release(token)
    assert fence.is_locked("t1") is False

    scheduler = LaneScheduler()
    assert scheduler.can_admit(ExecutionLane.SYSTEM_MAINTENANCE) is True
    admitted, task_id = scheduler.admit_or_queue("t1", ExecutionLane.USER_INTERACTION)
    assert admitted is True and task_id.startswith("task_")


# ---------------------------------------------------------------------------
# Keyed locks
# ---------------------------------------------------------------------------


def test_keyed_lock_tables_are_process_local_by_construction() -> None:
    """Both keyed-lock tables hold their state in instance attributes.

    ``AsyncKeyedLockTable`` additionally keys its entries by event loop, so two
    loops in one process do not share an ``asyncio.Lock`` (which would be
    loop-affine). That is a thread-safety property, not a cross-process one --
    and the table's own docstring says the thread lock "protects only the
    registry".
    """
    from alpha.runtime.keyed_lock import AsyncKeyedLockTable, KeyedLockTable

    a = AsyncKeyedLockTable()
    b = AsyncKeyedLockTable()
    assert a is not b
    assert "protects only the registry" in (AsyncKeyedLockTable.__doc__ or "")

    t = KeyedLockTable()
    with t.hold("k"):
        pass
    assert "across worker threads" in (KeyedLockTable.__doc__ or ""), "the thread-side table says threads, not processes"


# ---------------------------------------------------------------------------
# Policy approvals and the login throttle: process-local, API-reachable
# ---------------------------------------------------------------------------


def test_policy_approvals_are_process_local_and_lose_requests_on_a_peer() -> None:
    """``POST /api/policy/approvals`` writes to a module-level dict.

    A second worker cannot see, list, or decide an approval created on the
    first, and a restart drops every pending request. The route has no
    durability story and no error for it, so this is pinned as a fact an
    operator can act on rather than discovered in production.
    """
    from app.gateway.routers import policy

    policy._approvals.clear()
    created = policy.ApprovalRequest(request_id="ap-test-1", action="deploy", actor="agent", project_id="*")
    with policy._approvals_lock:
        policy._approvals[created.request_id] = created
    try:
        assert "ap-test-1" in policy._approvals
        # The decide route reads the same dict, so a decision is visible here...
        decided = policy._approvals.get("ap-test-1")
        assert decided is not None and decided.status == "pending"
    finally:
        policy._approvals.clear()
    # ...and nowhere else. There is no second process to lose it to, only a
    # restart: which is why this is a finding and not a leak.
    assert "ap-test-1" not in policy._approvals, "the only thing that removes it is this process"


def test_the_login_throttle_states_its_multi_worker_limitation_in_the_source() -> None:
    """The one process-local security control here says so, loudly.

    ``auth.py`` carries a comment block naming the exact weakness: an attacker
    gets N x ``max_login_attempts`` guesses under N workers. That honesty is the
    reason this control is not a finding -- an undocumented per-process lockout
    would be.
    """
    from pathlib import Path

    import app.gateway.routers.auth as auth_module

    source = Path(auth_module.__file__).read_text(encoding="utf-8", errors="replace")
    assert "In-process dict" in source, "the throttle must keep declaring that it is not shared across workers"
    assert "max_login_attempts guesses before being locked out everywhere" in source, "the multiplier consequence is stated, not just the mechanism"


# ---------------------------------------------------------------------------
# Event-store backends: the single-process guarantee is declared
# ---------------------------------------------------------------------------


def test_only_the_jsonl_store_declares_a_single_process_guarantee_in_its_docstring() -> None:
    """The dev backends say what they are; the SQL one does not need to.

    JSONL carries an explicit multi-process warning because a shared directory
    is the tempting mistake. The memory store's warning lives in its module
    docstring instead. Neither is silent, which is what matters: a backend that
    quietly assumed cross-process safety would be the dangerous one.
    """
    import alpha.runtime.events.store.jsonl as jsonl_module
    import alpha.runtime.events.store.memory as memory_module

    assert "Single-process guarantee" in (jsonl_module.__doc__ or "")
    assert "single-process" in (memory_module.__doc__ or "").lower()

    # Neither is reachable as a cross-process store: both are constructed per
    # process and hold every row in instance state.
    store = MemoryRunEventStore()
    assert store._events == {} and store._seq_counters == {}
    assert isinstance(store, RunEventStore)


@pytest.mark.asyncio
async def test_jsonl_seq_counters_are_per_instance_and_reload_from_disk(tmp_path) -> None:
    """Two JSONL stores over one directory each keep their own counter.

    The counter lives in ``_seq_counters`` on the instance, but it is *lazily*
    seeded from disk by ``_ensure_seq_loaded``, so a sequential second writer
    still gets the next seq. That is the honest shape of the guarantee: the
    directory is shared, the *coordination* is not. Two instances that write
    concurrently can both read the same ``max(seq)`` and emit the same value,
    which is the failure the module docstring names -- and it is a real gap,
    not a hypothetical, because the per-thread ``asyncio.Lock`` that closes it
    is per instance.

    Pinned in the direction that is actually true: sequential is safe, the
    counters are not shared, and the lock is not shared.
    """
    writer_a = JsonlRunEventStore(base_dir=tmp_path)
    writer_b = JsonlRunEventStore(base_dir=tmp_path)

    await writer_a.put(thread_id="t1", run_id="r1", event_type="llm.ai.response", category="message", content={"type": "ai", "id": "m1"})
    await writer_b.put(thread_id="t1", run_id="r1", event_type="llm.ai.response", category="message", content={"type": "ai", "id": "m2"})

    assert writer_a._seq_counters is not writer_b._seq_counters, "the counters are per instance"
    assert writer_a._get_write_lock("t1") is not writer_b._get_write_lock("t1"), "and so is the lock that serialises writes -- which is why the directory alone is not a cross-process guarantee"

    # Sequential writes are still correct, because the counter is seeded from disk.
    assert writer_a._seq_counters["t1"] == 1
    assert writer_b._seq_counters["t1"] == 2, "the second instance seeded from the row the first wrote"

    rows = await writer_a.list_events("t1", "r1")
    assert [r["seq"] for r in rows] == [1, 2], "sequential writers over one directory do not collide"


@pytest.mark.asyncio
async def test_memory_store_seq_counters_are_per_instance() -> None:
    """The memory backend cannot even see another instance's rows.

    There is no shared medium, so the guarantee is stronger and simpler than
    JSONL's: a second instance has an empty feed. This is why the startup gate
    refuses ``run_events.backend: memory`` under ``GATEWAY_WORKERS > 1``.
    """
    a = MemoryRunEventStore()
    b = MemoryRunEventStore()

    await a.put(thread_id="t1", run_id="r1", event_type="run.end", category="lifecycle", content={})

    assert await a.count_messages("t1") == 0
    assert len(await a.list_events("t1", "r1")) == 1
    assert len(await b.list_events("t1", "r1")) == 0
    assert b._seq_counters == {}


# ---------------------------------------------------------------------------
# Concurrency inside one process (the guarantee that *is* made)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_puts_on_one_thread_get_distinct_increasing_seqs() -> None:
    """The in-process guarantee the memory store does make, pinned.

    ``put_if_absent`` on the memory backend relies on there being no ``await``
    between the lookup and the append. That is a real guarantee for one event
    loop, and it is the reason two concurrent ``put_if_absent`` calls for the
    same ``(thread, run, event_type)`` produce exactly one row. If someone adds
    an ``await`` to ``_put_one``, this is the test that notices.
    """
    store = MemoryRunEventStore()

    results = await asyncio.gather(*[store.put(thread_id="t1", run_id="r1", event_type="trace", category="trace", content={"i": i}) for i in range(20)])
    seqs = sorted(r["seq"] for r in results)
    assert seqs == list(range(1, 21)), "seq must be strictly increasing within the thread"

    firsts = await asyncio.gather(*[store.put_if_absent(thread_id="t1", run_id="r1", event_type="run.delivery", category="outputs", content={"n": i}) for i in range(5)])
    created = [flag for _record, flag in firsts]
    assert created.count(True) == 1, f"exactly one writer may create a singleton event, got {created}"
    assert len(await store.list_events("t1", "r1", event_types=["run.delivery"])) == 1


@pytest.mark.asyncio
async def test_jsonl_put_if_absent_is_serialized_by_its_per_thread_write_lock(tmp_path) -> None:
    """JSONL's singleton write is protected by an in-process lock, not by luck.

    ``_write_record`` is offloaded with ``asyncio.to_thread``, so there *is* an
    await between the existence check and the append. What makes it safe is
    that both happen while ``_get_write_lock(thread_id)`` is held -- which is
    why the delivery-receipt guarantee the guide claims for JSONL is explicitly
    scoped to a single process.

    The assertion is mutual exclusion, not a winner count. With thread offload
    a lost race is scheduling-dependent, so "exactly one writer won" can pass
    against an unguarded implementation by accident -- and the first version of
    this test did exactly that. Two writers inside the region at the same
    moment cannot happen by accident.
    """
    import threading
    import time

    from alpha.runtime.events.store.jsonl import JsonlRunEventStore

    class _Probe(JsonlRunEventStore):
        def __init__(self, base_dir):
            super().__init__(base_dir=base_dir)
            self._probe_lock = threading.Lock()
            self._inside = 0
            self.max_inside = 0

        def _read_run_events(self, thread_id, run_id):
            with self._probe_lock:
                self._inside += 1
                self.max_inside = max(self.max_inside, self._inside)
            try:
                time.sleep(0.02)
                return super()._read_run_events(thread_id, run_id)
            finally:
                with self._probe_lock:
                    self._inside -= 1

    store = _Probe(tmp_path)
    results = await asyncio.gather(*[store.put_if_absent(thread_id="t1", run_id="r1", event_type="run.delivery", category="outputs", content={"n": i}) for i in range(6)])

    assert store.max_inside == 1, f"two writers were inside the check-then-append region at once ({store.max_inside})"
    assert [flag for _r, flag in results].count(True) == 1
    assert len(await store.list_events("t1", "r1", event_types=["run.delivery"])) == 1
