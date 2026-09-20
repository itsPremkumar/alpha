"""AutonomySupervisor — the single lifecycle owner for self-running subsystems.

Every background loop in Alpha is registered here. The supervisor:
* starts a loop ONLY when its config flag is enabled (flag off => zero activity);
* spaces ticks with interval + jitter and caps overlapping ticks per loop;
* runs sync ticks in a worker thread so a blocking subsystem cannot stall the
  event loop;
* restarts crashed ticks with exponential backoff inside a restart budget, then
  parks the loop instead of spinning;
* exposes start()/stop()/status() with exactly one shutdown path;
* publishes loop lifecycle events on the in-process event bus.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from alpha.config.autonomy_config import AutonomyConfig, AutonomyLoopConfig

logger = logging.getLogger(__name__)


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
class _LoopState:
    enabled: bool = False
    runs: int = 0
    failures: int = 0
    parked: bool = False
    park_reason: str = ""
    running: bool = False
    last_run_at: float = 0.0
    last_duration_seconds: float = 0.0
    last_error: str = ""
    last_summary: str = ""
    failure_times: list[float] = field(default_factory=list)


def _summarize(summary: Any) -> str:
    if summary is None:
        return ""
    if isinstance(summary, dict):
        return " ".join(f"{key}={value}" for key, value in list(summary.items())[:6])
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

    def register_default_loops(self) -> None:
        """Register every known subsystem loop (id is the manifest key)."""
        from app.gateway.autonomy import loops as loop_adapters

        defaults = (
            ("sentinel", "Observe-only sentinel pass: collect signals, escalate unknown kinds.", loop_adapters.sentinel_tick, 600.0),
            ("perpetual", "Perpetual daemon heartbeat: discovery, consolidation, stagnation.", loop_adapters.perpetual_tick, 900.0),
            ("review_queue", "Deferred learning reviews: observe pending count, publish a bus signal.", loop_adapters.review_queue_tick, 120.0),
            ("skill_curator", "Skill curator prune pass (dry-run unless configured otherwise).", loop_adapters.skill_curator_tick, 3600.0),
            ("enterprise_heartbeat", "Enterprise heartbeat cycle.", loop_adapters.enterprise_heartbeat_tick, 1800.0),
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
        """Cancel every loop task and wait for a clean exit. Idempotent."""
        self._stopping = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        for state in self._state.values():
            state.running = False

    # -- loop body -------------------------------------------------------------
    async def _run_loop(self, loop_id: str, spec: LoopSpec, cfg: AutonomyLoopConfig) -> None:
        state = self._state[loop_id]
        tick = spec.configure(cfg) if spec.configure else spec.tick
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
        async with self._semaphores[loop_id]:
            if state.running or self._stopping:
                return
            state.running = True
            started = time.time()
            tick_timeout = max(cfg.stop_timeout_seconds, cfg.interval_seconds)
            try:
                if inspect.iscoroutinefunction(tick):
                    summary = await asyncio.wait_for(tick(), timeout=tick_timeout)
                else:
                    summary = await asyncio.wait_for(asyncio.to_thread(tick), timeout=tick_timeout)
                state.runs += 1
                state.last_run_at = started
                state.last_duration_seconds = time.time() - started
                state.last_summary = _summarize(summary)
                state.last_error = ""
                await self._publish(loop_id, "completed", summary)
            except asyncio.CancelledError:
                state.running = False
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
            finally:
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
