"""AutonomySupervisor — the single lifecycle owner for self-running subsystems.

Every background loop in Alpha is registered here. The supervisor:
* starts a loop ONLY when its config flag is enabled (flag off => zero activity);
* spaces ticks with interval + jitter and caps overlapping ticks per loop;
* runs sync ticks in a worker thread so a blocking subsystem cannot stall the
  event loop;
* restarts crashed ticks with exponential backoff inside a restart budget, then
  parks the loop instead of spinning;
* treats a tick that outlives its deadline as still in flight — a worker thread
  cannot be interrupted, so the loop keeps its slot and ``stop()`` waits a
  bounded grace period for it instead of draining the stack underneath it;
* exposes start()/stop()/status() with exactly one shutdown path;
* publishes loop lifecycle events on the in-process event bus.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from alpha.config.autonomy_config import AutonomyConfig, AutonomyLoopConfig

logger = logging.getLogger(__name__)


def _fleet_admits_tick(loop_id: str) -> bool:
    """True when fleet control allows this loop to run a tick right now.

    Fails closed: an unreadable control state, or one this process cannot
    interpret, stops the loop rather than running it. A stop mechanism that
    fails open is not a stop mechanism.
    """
    try:
        from alpha.runtime.control import assert_admissible, read_state
        from alpha.runtime.estop import get_estop_manager

        # The pre-existing sentinel is honoured as well, so an operator who
        # engaged either mechanism gets the same fleet-wide effect. Two sources
        # because `alpha.bots.kill_switch` is in-memory and this sentinel is
        # durable; both are consulted rather than one subsuming the other, and
        # neither is removed.
        if get_estop_manager().is_engaged():
            logger.warning("Fleet ESTOP engaged; skipping autonomy loop tick %s", loop_id)
            return False

        state = read_state()
        if state.mode.value != "run":
            logger.warning("Fleet control is %s; skipping autonomy loop tick %s", state.mode.value, loop_id)
            return False

        assert_admissible(f"autonomy loop {loop_id}")
        return True
    except Exception as exc:
        logger.error("Fleet control refused autonomy loop %s (fail-closed): %s", loop_id, exc)
        return False


async def _mod_admits_tick(loop_id: str) -> bool:
    """Run the shared Mods admission policy before executing an autonomy tick."""
    try:
        from alpha.mods.kernel import get_mod_kernel, require_mod_admission
        from alpha.mods.types import AlphaEvent, CorrelationContext

        event = AlphaEvent(
            name="autonomy.tick",
            payload={"loop_id": loop_id},
            correlation=CorrelationContext.create(task_id=loop_id),
            source="runtime:autonomy_supervisor",
        )
        await require_mod_admission(get_mod_kernel(), event)
        return True
    except Exception as exc:
        logger.error("Mod policy refused autonomy loop %s (fail-closed): %s", loop_id, exc)
        return False


@dataclass
class LoopSpec:
    """Registration entry for one background loop."""

    loop_id: str
    description: str
    tick: Callable[[], Any]
    default_interval_seconds: float = 300.0
    # Optional per-tick override (e.g. curator dry-run flag); receives the loop config.
    configure: Callable[[AutonomyLoopConfig], Callable[[], Any]] | None = None


@dataclass
class _TickRun:
    """One tick execution and the two places its lifetime can end.

    A synchronous tick ends when its worker thread returns; an awaitable one ends
    when its task does, and has no worker at all. ``worker_done`` therefore starts
    true — there is nothing to wait for — and is cleared only once the execution
    has actually put a thread on the executor. A task that is cancelled while its
    thread keeps running has ended only half of its lifetime, so the counters move
    only once both halves are down; otherwise a loop reports itself idle while it
    is still working.
    """

    loop_id: str
    work: asyncio.Task[Any] | None = None
    task_done: bool = False
    worker_done: bool = True
    settled: bool = False


@dataclass
class _LoopState:
    enabled: bool = False
    runs: int = 0
    failures: int = 0
    parked: bool = False
    park_reason: str = ""
    running: bool = False
    #: Ticks this loop started that have not finished. A value above the loop's
    #: `max_concurrent` is a bug, and a value that stays above zero with
    #: `task_alive` false is a worker the supervisor is still waiting on.
    in_flight: int = 0
    #: Ticks the supervisor stopped waiting for while they were still running.
    overruns: int = 0
    last_run_at: float = 0.0
    last_duration_seconds: float = 0.0
    last_error: str = ""
    last_summary: str = ""
    failure_times: list[float] = field(default_factory=list)


def _summarize(summary: Any) -> str:
    if summary is None:
        return ""
    if isinstance(summary, dict):
        # Health views must not hide terminal outcomes behind a fixed prefix.
        # APEX returns several progress counters before `failed` and `blocked`,
        # so keeping only the first six fields made a busy-but-failing loop look
        # clean. Put operationally important outcomes first, then retain the
        # original order for the rest of the summary.
        priority = ("error", "failed", "blocked", "budget_exhausted", "errors")
        keys = [key for key in priority if key in summary]
        keys.extend(key for key in summary if key not in keys)
        parts: list[str] = []
        for key in keys:
            value = summary[key]
            if isinstance(value, (dict, list, tuple, set, frozenset)):
                value = len(value)
            parts.append(f"{key}={value}")
        return " ".join(parts)[:300]
    return str(summary)[:300]


def spec_loop_concurrency(spec: LoopSpec, config: AutonomyConfig) -> int:
    cfg = config.loop_config(spec.loop_id)
    return max(1, int(cfg.max_concurrent))


class AutonomySupervisor:
    """Owns every background loop. One start, one stop, one status."""

    def __init__(self, config: AutonomyConfig | None = None) -> None:
        self._config = config or AutonomyConfig()
        self._specs: dict[str, LoopSpec] = {}
        self._state: dict[str, _LoopState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        # Tick executions the supervisor has started but not seen finish. The set
        # is what `stop()` drains; it is deliberately NOT the same thing as the
        # supervising loop task, because cancelling that task cannot end a tick.
        self._inflight: dict[str, set[asyncio.Task[Any]]] = {}
        # Worker threads currently inside a synchronous tick. Recorded by the
        # thread itself and cleared in its own `finally`, so it stays accurate
        # after the task awaiting that thread has been cancelled — the one
        # lifetime a task cannot report.
        self._workers: dict[str, set[int]] = {}
        self._stopping = False
        self._started_at = 0.0

    # -- registration ----------------------------------------------------------
    def register(self, spec: LoopSpec) -> None:
        """Idempotently register a loop. Re-registration replaces the tick only."""
        existing = self._specs.get(spec.loop_id)
        if existing is not None:
            existing.tick = spec.tick
            existing.configure = spec.configure
            return
        self._specs[spec.loop_id] = spec
        self._state[spec.loop_id] = _LoopState()
        self._semaphores[spec.loop_id] = asyncio.Semaphore(spec_loop_concurrency(spec, self._config))
        self._inflight[spec.loop_id] = set()
        self._workers[spec.loop_id] = set()

    def register_default_loops(self) -> None:
        """Register every known subsystem loop (id is the manifest key)."""
        from app.gateway.autonomy import loops as loop_adapters

        defaults = (
            ("sentinel", "Observe-only sentinel pass: collect signals, escalate unknown kinds.", loop_adapters.sentinel_tick, 600.0),
            ("perpetual", "Perpetual daemon heartbeat: discovery, consolidation, stagnation.", loop_adapters.perpetual_tick, 900.0),
            ("review_queue", "Deferred learning reviews: observe pending count, publish a bus signal.", loop_adapters.review_queue_tick, 120.0),
            ("skill_curator", "Skill curator prune pass (dry-run unless configured otherwise).", loop_adapters.skill_curator_tick, 3600.0),
            ("enterprise_heartbeat", "Enterprise heartbeat cycle.", loop_adapters.enterprise_heartbeat_tick, 1800.0),
            ("swarm_status", "Telemetry-only swarm tick: report active swarm state.", loop_adapters.swarm_status_tick, 300.0),
            ("free_models_sync", "Daily discovery and health check for keyless free LLM models.", loop_adapters.free_models_sync_tick, 86400.0),
            ("company_operations", "Bounded sweep of Company OS loops: observe, plan, act, verify, ledger.", loop_adapters.company_operations_tick, 300.0),
            ("self_update", "Opt-in GitHub source update check and guarded apply.", loop_adapters.self_update_tick, 21600.0),
            ("apex", "APEX executive cycle with idempotent RunManager dispatch and run observation.", loop_adapters.apex_tick, 120.0),
            ("mission", "Fail-closed mission watchdog: park done-stuck/blocked missions (never loops, never dispatches).", loop_adapters.mission_tick, 300.0),
        )
        for loop_id, description, tick, interval in defaults:
            self.register(LoopSpec(loop_id=loop_id, description=description, tick=tick, default_interval_seconds=interval))

    # -- lifecycle ---------------------------------------------------------------
    async def start(self) -> None:
        """Start every enabled loop. Disabled loops create no task at all."""
        if self._tasks:
            logger.warning("AutonomySupervisor.start() called twice; ignoring")
            return
        self._stopping = False
        self._started_at = time.time()
        for loop_id, spec in self._specs.items():
            loop_cfg = self._config.loop_config(loop_id)
            self._state[loop_id].enabled = loop_cfg.enabled
            if not (self._config.enabled and loop_cfg.enabled):
                logger.info(
                    "Autonomy loop '%s' disabled (autonomy.enabled=%s, loop.enabled=%s)",
                    loop_id,
                    self._config.enabled,
                    loop_cfg.enabled,
                )
                continue
            self._tasks[loop_id] = asyncio.create_task(self._run_loop(loop_id, spec, loop_cfg), name=f"autonomy:{loop_id}")
            logger.info("Autonomy loop '%s' started (interval=%.0fs)", loop_id, loop_cfg.interval_seconds)

    async def stop(self) -> None:
        """Cancel every loop task and wait for a clean exit. Idempotent.

        Cancelling a supervising loop does not end the tick it was awaiting: a
        synchronous tick keeps running on its worker thread, and an awaitable one
        would keep running unless it is cancelled here. So after the loop tasks
        are down, every tick still in flight is cancelled and awaited for a
        bounded grace period. That grace period is the only thing standing
        between a loop tick and a half-stopped stack: the Gateway stops the
        supervisor first precisely so its loops never observe one.
        """
        self._stopping = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        await self._drain_in_flight_ticks()
        for loop_id, state in self._state.items():
            # A tick whose worker never returned must not be reported as idle.
            if not self._workers.get(loop_id):
                state.running = False

    async def _drain_in_flight_ticks(self) -> None:
        """Cancel and await ticks that outlived their supervising loop.

        Bounded by each loop's ``stop_timeout_seconds``, so a hung worker cannot
        hold the Gateway's shutdown open. A worker that outlives the grace period
        is logged and stays visible in ``status()`` (``running`` and
        ``in_flight`` remain set) rather than being silently forgotten.

        The wait is over ``_inflight`` rather than over task liveness: a
        cancelled synchronous tick leaves its task finished and its thread
        running, and that thread is the execution. Where every task is already
        down, only the thread can report its own exit, so the drain polls it.
        """
        for loop_id in sorted(self._inflight):
            for work in list(self._inflight[loop_id]):
                if not work.done():
                    work.cancel()
            grace_seconds = self._config.loop_config(loop_id).stop_timeout_seconds
            deadline = time.monotonic() + grace_seconds
            while self._inflight.get(loop_id) or self._workers.get(loop_id):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pending = [work for work in self._inflight[loop_id] if not work.done()]
                if pending:
                    await asyncio.wait(pending, timeout=remaining)
                else:
                    await asyncio.sleep(min(0.05, remaining))
            if self._inflight.get(loop_id) or self._workers.get(loop_id):
                logger.warning(
                    "Autonomy loop '%s' still has %d tick(s) executing after the %.0fs shutdown grace period",
                    loop_id,
                    len(self._inflight.get(loop_id, ())) + len(self._workers.get(loop_id, ())),
                    grace_seconds,
                )

    # -- loop body -------------------------------------------------------------
    async def _run_loop(self, loop_id: str, spec: LoopSpec, cfg: AutonomyLoopConfig) -> None:
        state = self._state[loop_id]
        try:
            tick = spec.configure(cfg) if spec.configure else spec.tick
        except Exception as exc:
            # A tick that cannot be configured leaves this task finished and the
            # loop permanently silent. Recording it here is what keeps a dead
            # loop distinguishable from an idle one.
            state.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("Autonomy loop '%s' could not configure its tick: %s", loop_id, state.last_error)
            return
        while not self._stopping:
            delay = cfg.interval_seconds + random.uniform(0, max(0.0, cfg.jitter_seconds))
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if self._stopping:
                return
            await self._tick_once(loop_id, cfg, tick, state)

    async def _tick_once(self, loop_id: str, cfg: AutonomyLoopConfig, tick: Callable[[], Any], state: _LoopState) -> None:
        if state.running or state.parked:
            return
        # Fleet control is checked here because this is the single choke point all
        # registered loops pass through — nine of them, per `register_default_loops`
        # below. The count is written as a word and not a numeral on purpose: it was
        # "eight" here while nine were registered, and a numeral in a comment about a
        # registry is a claim nothing checks. A tick that starts while the operator
        # has engaged ESTOP must not run, and a tick admitted before the stop must
        # not act on a superseded generation. This loop previously had no stop
        # mechanism at all: `autonomy.loops.*` flags decide whether a loop is
        # *registered*, not whether it is *allowed to run right now*.
        if not _fleet_admits_tick(loop_id):
            return
        # A missing or broken policy kernel must not allow autonomous work to
        # proceed. The fleet-control check above remains an independent gate.
        try:
            from alpha.mods.kernel import get_mod_kernel, require_mod_admission
            from alpha.mods.types import AlphaEvent, CorrelationContext

            mod_ev = AlphaEvent(
                name="autonomy.tick",
                payload={"loop_id": loop_id},
                correlation=CorrelationContext.create(task_id=f"loop:{loop_id}"),
                source="autonomy-supervisor",
            )
            await require_mod_admission(get_mod_kernel(), mod_ev)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Alpha Mod Kernel could not admit autonomy loop '%s'; skipping tick", loop_id, exc_info=True)
            return
        async with self._semaphores[loop_id]:
            if state.running or self._stopping:
                return
            if not _fleet_admits_tick(loop_id) or not await _mod_admits_tick(loop_id):
                return
            state.running = True
            state.in_flight += 1
            started = time.time()
            tick_timeout = max(cfg.stop_timeout_seconds, cfg.interval_seconds)
            run = _TickRun(loop_id)
            work = asyncio.create_task(self._execute_tick(run, tick, tick_timeout), name=f"autonomy-tick:{loop_id}")
            run.work = work
            self._inflight[loop_id].add(work)
            work.add_done_callback(lambda finished: self._tick_task_finished(run, finished))
            try:
                # `shield` is load-bearing: cancelling the supervising loop must
                # not be read as cancelling the tick, because for a
                # synchronous tick it is not. The tick keeps its slot until it
                # really ends, which is what stops the next interval from
                # starting a second tick over the first.
                summary = await asyncio.wait_for(asyncio.shield(work), timeout=tick_timeout)
                state.runs += 1
                state.last_run_at = started
                state.last_duration_seconds = time.time() - started
                state.last_summary = _summarize(summary)
                state.last_error = ""
                await self._publish(loop_id, "completed", summary)
            except asyncio.CancelledError:
                # Shutdown (or a cancelled supervisor): the tick itself is
                # drained and awaited by stop(), not dropped here.
                state.overruns += 1
                raise
            except Exception as exc:
                now = time.time()
                state.failures += 1
                state.failure_times.append(now)
                state.last_error = f"{type(exc).__name__}: {exc}"
                state.last_run_at = started
                window_start = now - cfg.restart_window_seconds
                state.failure_times = [t for t in state.failure_times if t >= window_start]
                logger.warning("Autonomy loop '%s' tick failed: %s", loop_id, state.last_error)
                if len(state.failure_times) >= cfg.restart_budget:
                    state.parked = True
                    state.park_reason = f"{len(state.failure_times)} failures within {cfg.restart_window_seconds:.0f}s"
                    logger.error("Autonomy loop '%s' parked: %s", loop_id, state.park_reason)
                if not work.done():
                    # The deadline passed while the tick was still going. Record
                    # the overrun so the slot it still occupies is visible, and
                    # let it finish: nothing about a worker thread can be hurried.
                    state.overruns += 1

    async def _execute_tick(self, run: _TickRun, tick: Callable[[], Any], tick_timeout: float) -> Any:
        """Run one tick under its deadline, keeping the loop's slot until it ends.

        The deadline is enforced here as well as in the awaiter, so a cancellable
        tick ends on its own. A synchronous tick cannot be interrupted at all, so
        this task stays alive until its worker thread returns — and that is the
        lifetime ``stop()`` waits on.
        """
        if inspect.iscoroutinefunction(tick):
            return await asyncio.wait_for(tick(), timeout=tick_timeout)
        # Set before the dispatch, with no await between: a cancellation that
        # lands past this point owes a worker thread, and one that lands before it
        # owes nothing. Either way the run settles, so no slot can be stranded.
        run.worker_done = False
        return await asyncio.wait_for(asyncio.to_thread(self._run_on_worker, run, tick), timeout=tick_timeout)

    def _run_on_worker(self, run: _TickRun, tick: Callable[[], Any]) -> Any:
        """Run a synchronous tick on its worker thread, recording the thread.

        The record is made and cleared inside the thread, so it outlives the
        task that awaits it: an abandoned worker thread is precisely the
        lifetime a task cannot report, and it is what ``stop()`` must wait for.
        """
        self._workers.setdefault(run.loop_id, set()).add(threading.current_thread().ident)
        try:
            return tick()
        finally:
            workers = self._workers.get(run.loop_id)
            if workers is not None:
                workers.discard(threading.current_thread().ident)
            self._tick_worker_finished(run)

    def _tick_task_finished(self, run: _TickRun, work: asyncio.Task[Any]) -> None:
        """Record that a tick's awaiting task ended, and close it if it may."""
        if not work.cancelled():
            # Retrieve, so a tick abandoned at its deadline is not logged a
            # second time as an unretrieved exception.
            work.exception()
        run.task_done = True
        self._close_tick(run)

    def _tick_worker_finished(self, run: _TickRun) -> None:
        """Record that a tick's worker thread returned, and close it if it may."""
        run.worker_done = True
        self._close_tick(run)

    def _close_tick(self, run: _TickRun) -> None:
        """Release a tick's slot once every part of its lifetime has ended.

        The task stays in ``_inflight`` until this point rather than when it
        finishes, because a cancelled synchronous tick leaves its task finished
        and its thread running: dropping the task there would make the
        execution invisible to ``stop()`` and leave ``in_flight`` stuck above
        zero for the rest of the process life.
        """
        if run.settled or not (run.task_done and run.worker_done):
            return
        run.settled = True
        pending = self._inflight.get(run.loop_id)
        if pending is not None and run.work is not None:
            pending.discard(run.work)
        state = self._state.get(run.loop_id)
        if state is None:
            return
        state.in_flight = max(0, state.in_flight - 1)
        if state.in_flight == 0 and not self._workers.get(run.loop_id):
            state.running = False

    # -- telemetry ---------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Full supervisor status: per-loop counters plus task liveness."""
        return {
            "enabled": self._config.enabled,
            "started_at": self._started_at,
            "loops": {
                loop_id: {
                    "description": spec.description,
                    **{
                        key: getattr(self._state[loop_id], key)
                        for key in (
                            "enabled",
                            "runs",
                            "failures",
                            "parked",
                            "park_reason",
                            "running",
                            "in_flight",
                            "overruns",
                            "last_run_at",
                            "last_duration_seconds",
                            "last_error",
                            "last_summary",
                        )
                    },
                    "task_alive": loop_id in self._tasks and not self._tasks[loop_id].done(),
                }
                for loop_id, spec in self._specs.items()
            },
        }

    async def _publish(self, loop_id: str, outcome: str, summary: Any) -> None:
        try:
            from alpha.events.bus import get_event_bus

            await get_event_bus().publish(
                f"autonomy.loop.{outcome}",
                {"loop_id": loop_id, "summary": _summarize(summary)},
                source="autonomy-supervisor",
            )
        except Exception:  # bus problems must never affect loops
            logger.debug("Autonomy event publish failed", exc_info=True)


_supervisor: AutonomySupervisor | None = None


def get_autonomy_supervisor(config: AutonomyConfig | None = None) -> AutonomySupervisor:
    """Process-wide supervisor singleton (registers the default loops once)."""
    global _supervisor
    if _supervisor is None:
        _supervisor = AutonomySupervisor(config)
        _supervisor.register_default_loops()
    elif config is not None:
        _supervisor._config = config
    return _supervisor
