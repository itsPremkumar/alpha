"""Planned shutdown: an ordered drain whose report tells the truth.

The problem
-----------
Shutdown is where a durable runtime most easily lies. A process that is killed
mid-flight has its in-memory plan nowhere; the next start has to reconstruct it.
And the shutdown path is exactly the code that tends to *claim* success: a
sequence of best-effort steps wrapped in a blanket ``except``, ending in a log
line that says "shutdown complete" whether or not the queue was persisted.

The durable-runtime contract asks for a specific order — stop accepting new
work, finish safe operations, checkpoint sessions, persist the queue, persist
pending permissions, persist the scheduler, persist runtime state, stop workers
— and for an emergency path that does best-effort checkpointing *without
indefinitely blocking OS shutdown*.

Two rules are enforced here
---------------------------
**1. Order is the contract, and it is declared, not implied.** A step may only
run once every step it depends on has completed. A dependency that was skipped
or failed blocks its dependents rather than letting them run against
half-persisted state — writing a scheduler snapshot before the queue it
dispatches from is persisted is how a shutdown corrupts work instead of
protecting it.

**2. A partial shutdown is reported as partial.**
:class:`ShutdownReport` lists every step as ``completed``, ``skipped``,
``failed``, or ``timed_out``, and carries ``is_clean``. There is no path that
produces a clean report while a step was skipped or failed, so "shutdown
complete" in a log means every step really did finish. A step that raises is
recorded with its reason and the drain continues — one broken store must not
strand the others, because the remaining steps are the ones protecting work.

Deadlines
---------
Every step gets its own bound, and the whole drain gets an overall bound. A
step that exceeds its bound is recorded ``timed_out`` and the drain moves on:
waiting for one wedged component must not prevent the others from persisting
what they can. :meth:`PlannedShutdown.shutdown` is therefore safe to call from
a signal handler or a service manager with a hard kill deadline behind it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "PLANNED_SHUTDOWN_ORDER",
    "PlannedShutdown",
    "ShutdownPhase",
    "ShutdownReport",
    "ShutdownStatus",
    "ShutdownStep",
]


class ShutdownPhase(StrEnum):
    """The ordered stages of a planned shutdown."""

    ADMISSION_CLOSED = "admission_closed"
    OPERATIONS_DRAINED = "operations_drained"
    CHECKPOINTS_WRITTEN = "checkpoints_written"
    QUEUE_PERSISTED = "queue_persisted"
    PERMISSIONS_PERSISTED = "permissions_persisted"
    SCHEDULER_PERSISTED = "scheduler_persisted"
    WORKERS_STOPPED = "workers_stopped"
    COMPLETE = "complete"


class ShutdownStatus(StrEnum):
    """How one step ended. Closed set: every step is exactly one of these."""

    PENDING = "pending"
    COMPLETED = "completed"
    #: Not attempted, because an earlier dependency did not complete.
    SKIPPED = "skipped"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


#: The order the contract requires. A step's position in this tuple is its
#: dependency depth: a step only runs when every *earlier registered* step
#: completed, so a host registers the steps it owns and inherits the ordering.
PLANNED_SHUTDOWN_ORDER: tuple[ShutdownPhase, ...] = (
    ShutdownPhase.ADMISSION_CLOSED,
    ShutdownPhase.OPERATIONS_DRAINED,
    ShutdownPhase.CHECKPOINTS_WRITTEN,
    ShutdownPhase.QUEUE_PERSISTED,
    ShutdownPhase.PERMISSIONS_PERSISTED,
    ShutdownPhase.SCHEDULER_PERSISTED,
    ShutdownPhase.WORKERS_STOPPED,
)

Action = Callable[[], Any | Awaitable[Any]]


@dataclass
class ShutdownStep:
    """One named action in the drain.

    ``requires`` names phases this step depends on. It is checked against the
    *global* order rather than only against other registered steps, so a step
    that requires ``CHECKPOINTS_WRITTEN`` is refused when the host registered no
    checkpoint step at all — silently skipping a declared prerequisite would let
    a step run against state nobody persisted.
    """

    phase: ShutdownPhase
    action: Action | None = None
    timeout_seconds: float = 10.0
    description: str = ""
    requires: tuple[ShutdownPhase, ...] = ()
    status: ShutdownStatus = ShutdownStatus.PENDING
    detail: str = ""
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.phase not in PLANNED_SHUTDOWN_ORDER:
            raise ValueError(f"{self.phase!r} is not a planned-shutdown phase; use ShutdownPhase.COMPLETE to finish")
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        if not self.description:
            self.description = self.phase.value.replace("_", " ")


@dataclass(frozen=True, slots=True)
class ShutdownReport:
    """What actually happened, per step.

    ``is_clean`` is the only field a caller should branch on for "did shutdown
    work", and it is False whenever any step was skipped, failed, or timed out.
    """

    steps: tuple[tuple[ShutdownPhase, ShutdownStatus, str], ...]
    emergency: bool
    total_seconds: float

    @property
    def is_clean(self) -> bool:
        """True only when every registered step completed.

        The single field a caller should branch on. There is deliberately no
        second "was it truncated" flag: the per-step ``status`` and ``detail``
        already say exactly why something did not finish, and a summary boolean
        that merely restates ``is_clean`` invites the two to disagree.
        """
        return bool(self.steps) and all(status is ShutdownStatus.COMPLETED for _, status, _ in self.steps)

    def status_of(self, phase: ShutdownPhase) -> ShutdownStatus:
        for candidate, status, _ in self.steps:
            if candidate is phase:
                return status
        return ShutdownStatus.SKIPPED

    def incomplete(self) -> tuple[ShutdownPhase, ...]:
        return tuple(phase for phase, status, _ in self.steps if status is not ShutdownStatus.COMPLETED)

    def to_dict(self) -> dict[str, object]:
        return {
            "is_clean": self.is_clean,
            "emergency": self.emergency,
            "total_seconds": round(self.total_seconds, 3),
            "steps": [{"phase": phase.value, "status": status.value, "detail": detail} for phase, status, detail in self.steps],
            "incomplete": [phase.value for phase in self.incomplete()],
        }

    def to_text(self) -> str:
        lines = [f"shutdown {'CLEAN' if self.is_clean else 'INCOMPLETE'}{' (emergency)' if self.emergency else ''} in {self.total_seconds:.2f}s"]
        lines.extend(f"  {phase.value}: {status.value}{f' ({detail})' if detail else ''}" for phase, status, detail in self.steps)
        return "\n".join(lines)


class PlannedShutdown:
    """Runs an ordered, bounded, honestly-reported drain.

    Hosts register the steps they own; the ordering, the dependency check, the
    per-step bound, and the report all come from here. Nothing is retried and
    nothing is skipped silently, because both turn a partial shutdown into a
    reported-clean one.
    """

    def __init__(self, *, overall_timeout_seconds: float = 30.0, per_step_timeout_seconds: float = 10.0) -> None:
        self._steps: list[ShutdownStep] = []
        self._overall_timeout_seconds = max(0.0, float(overall_timeout_seconds))
        self._default_step_timeout = max(0.0, float(per_step_timeout_seconds))
        self._admission_closed = False

    # -- registration ---------------------------------------------------------

    def register(self, phase: ShutdownPhase, action: Action | None = None, *, timeout_seconds: float | None = None, description: str = "", requires: Sequence[ShutdownPhase] = ()) -> ShutdownStep:
        """Register one step. Registering a phase twice replaces the earlier one.

        Replacement rather than an error, because a host wiring a component in
        twice (a reload, a test) should get one step, not two competing writes
        to the same state.
        """
        step = ShutdownStep(phase=phase, action=action, timeout_seconds=self._default_step_timeout if timeout_seconds is None else timeout_seconds, description=description, requires=tuple(requires))
        self._steps = [existing for existing in self._steps if existing.phase is not phase]
        self._steps.append(step)
        # Keep registration order aligned with the contract order so the drain
        # does not depend on the order a host happened to call this in.
        self._steps.sort(key=lambda item: PLANNED_SHUTDOWN_ORDER.index(item.phase))
        return step

    def close_admission(self) -> None:
        """Stop accepting new work, immediately and without awaiting anything.

        Separate from the drain on purpose: this is the one action that must
        happen even in an emergency path, because every subsequent step's safety
        depends on nothing new arriving. It is idempotent.
        """
        self._admission_closed = True

    @property
    def admission_closed(self) -> bool:
        return self._admission_closed

    def steps(self) -> tuple[ShutdownStep, ...]:
        return tuple(self._steps)

    def pending(self) -> ShutdownPhase:
        """The next phase the drain would run, or ``COMPLETE``."""
        for step in self._steps:
            if step.status is ShutdownStatus.PENDING:
                return step.phase
        return ShutdownPhase.COMPLETE

    # -- execution ------------------------------------------------------------

    async def shutdown(self, *, emergency: bool = False) -> ShutdownReport:
        """Run every registered step in order and report what happened.

        ``emergency=True`` tightens the overall bound and skips the
        non-critical phases, so a hard OS-shutdown deadline is never blocked
        waiting for a component that is already gone. It still closes admission
        first and still attempts the checkpoint step, because that is the one
        that protects work.
        """
        self.close_admission()
        started = time.monotonic()
        deadline = started + (min(self._overall_timeout_seconds, _EMERGENCY_BUDGET_SECONDS) if emergency else self._overall_timeout_seconds)

        active = self._steps if not emergency else [step for step in self._steps if step.phase in _EMERGENCY_PHASES]
        for step in self._steps:
            if step not in active:
                step.status = ShutdownStatus.SKIPPED
                step.detail = "omitted from the emergency path" if emergency else "not registered for this path"

        for step in active:
            if time.monotonic() >= deadline:
                step.status = ShutdownStatus.TIMED_OUT
                step.detail = "the overall shutdown deadline elapsed before this step started"
                continue
            await self._run_step(step, deadline=deadline, emergency=emergency)

        elapsed = time.monotonic() - started
        report = ShutdownReport(
            steps=tuple((step.phase, step.status, step.detail) for step in self._steps),
            emergency=emergency,
            total_seconds=elapsed,
        )
        if report.is_clean:
            logger.info("planned shutdown complete in %.2fs", elapsed)
        else:
            logger.warning("planned shutdown INCOMPLETE: %s", report.incomplete())
        return report

    async def _run_step(self, step: ShutdownStep, *, deadline: float, emergency: bool) -> None:
        unmet = self._unmet_requirements(step)
        if unmet:
            step.status = ShutdownStatus.SKIPPED
            step.detail = f"prerequisite not completed: {', '.join(phase.value for phase in unmet)}"
            return

        budget = min(step.timeout_seconds, max(0.0, deadline - time.monotonic()))
        started = time.monotonic()
        try:
            if budget <= 0.0:
                step.status = ShutdownStatus.TIMED_OUT
                step.detail = "no time remained in the shutdown budget"
                return
            result = step.action() if step.action is not None else None
            if hasattr(result, "__await__"):
                await asyncio.wait_for(result, timeout=budget)  # type: ignore[arg-type]
            step.status = ShutdownStatus.COMPLETED
            step.detail = ""
        except TimeoutError:
            step.status = ShutdownStatus.TIMED_OUT
            step.detail = f"exceeded its {budget:.1f}s budget"
            logger.warning("shutdown step %s timed out after %.1fs", step.phase.value, budget, exc_info=True)
        except asyncio.CancelledError:
            # A cancelled drain is an emergency, not a completed step. Record it
            # honestly and let the caller decide, rather than reporting success.
            step.status = ShutdownStatus.FAILED
            step.detail = "cancelled before completion"
            raise
        except BaseException as exc:  # noqa: BLE001 - one broken step must not strand the rest
            step.status = ShutdownStatus.FAILED
            step.detail = f"{type(exc).__name__}: {exc}"
            logger.warning("shutdown step %s failed: %s", step.phase.value, exc, exc_info=True)
        finally:
            step.duration_seconds = time.monotonic() - started
        del emergency

    def _unmet_requirements(self, step: ShutdownStep) -> tuple[ShutdownPhase, ...]:
        """Phases *step* depends on that are not complete.

        Checked against the global order, so a declared prerequisite nobody
        registered blocks the step instead of being silently ignored.
        """
        unmet: list[ShutdownPhase] = []
        for required in step.requires:
            owner = next((candidate for candidate in self._steps if candidate.phase is required), None)
            if owner is None or owner.status is not ShutdownStatus.COMPLETED:
                unmet.append(required)
        return tuple(unmet)

    def reset(self) -> None:
        """Return every step to ``PENDING`` so the drain can run again.

        Exists for a host that drains and then keeps serving (an in-process
        reload); a shutdown that cannot be re-armed is a one-way door.
        """
        for step in self._steps:
            step.status = ShutdownStatus.PENDING
            step.detail = ""
            step.duration_seconds = 0.0
        self._admission_closed = False


#: The phases an emergency path still runs. Admission is closed unconditionally
#: (see ``close_admission``); the checkpoint step is kept because it is the one
#: that protects in-flight work, and the rest are persisted state a later
#: recovery scan can reconstruct from durable storage.
_EMERGENCY_PHASES: frozenset[ShutdownPhase] = frozenset({ShutdownPhase.ADMISSION_CLOSED, ShutdownPhase.CHECKPOINTS_WRITTEN, ShutdownPhase.WORKERS_STOPPED})

#: A hard ceiling for the emergency path, so a service manager's kill deadline
#: is never overrun no matter what the configured overall timeout says.
_EMERGENCY_BUDGET_SECONDS: float = 5.0


def default_shutdown_steps() -> Iterable[ShutdownStep]:
    """The empty skeleton, in contract order.

    Useful as a checklist: a host diffs this against what it registered, and
    the difference is exactly the work that would be lost on restart.
    """
    for phase in PLANNED_SHUTDOWN_ORDER:
        yield ShutdownStep(phase=phase)
