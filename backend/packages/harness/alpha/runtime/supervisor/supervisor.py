"""The supervisor: start a process, watch it, and know when to stop trying.

Shape
-----
:class:`ProcessSupervisor` is the *orchestration*, and it is deliberately thin
over two things that already exist and are tested on their own:

* :class:`~alpha.runtime.supervisor.policy.RestartLedger` decides whether to
  restart, wait, try safe mode, or give up. It never touches a process.
* :class:`~alpha.runtime.resilience.retry.RetryPolicy` owns the backoff maths.

Everything here is process lifecycle and evidence collection. That split is what
makes the crash-loop policy testable without spawning anything: a test drives
``RestartLedger`` with a ``ManualClock``, and a separate small set of tests
exercises the real spawn/watch path against a trivially-exiting child.

Why the child is not the source of truth
----------------------------------------
A supervised process is disposable; its work is not, and it is not what this
supervisor protects. The backend persists every task durably (LangGraph
checkpoints, the ``runs`` table, the ``run_events`` feed), and
``app.gateway.run_recovery.SafeRunRecoveryService`` reconstructs and resumes
unfinished sessions at startup. So a supervisor crash costs a restart delay, not
a task — *provided* the backend was allowed to recover. That is also why the
backend must never depend on the desktop UI being open: the UI is a viewer, and
the supervisor's job is to keep the runtime up independently of it.

Health validation, not just process liveness
--------------------------------------------
A process that is alive but not serving is worse than one that exited: it accepts
connections and fails them, and a naive liveness-only supervisor reports it as
healthy. So a start is only considered successful once ``health_check`` passes
within ``health_timeout_seconds``. A start that never becomes healthy is recorded
as :attr:`~alpha.runtime.supervisor.policy.SupervisorReason.HEALTH_FAILED`, which
consumes budget exactly like a crash — because to the rest of Alpha it is the
same event.

Safe mode
---------
Safe mode is a *reduced configuration*, not a retry: whatever the host's
``safe_mode_argv`` / ``safe_mode_env`` provide (typically fewer extensions, no MCP
autostart, no background loops). It is attempted **once** per crash episode. The
rationale is in :mod:`alpha.runtime.supervisor.policy`; the short version is that
for a startup failure there is nothing between "this configuration boots" and "it
does not", so a ladder would be a promise the supervisor cannot keep.

Diagnostics
-----------
:meth:`ProcessSupervisor.diagnostics` answers the six questions an operator
actually asks — what failed, why, where, what was Alpha doing, what it tried,
and what happens next — as a single report. It is derived from the ledger's
recorded history, so it is available *after* the supervisor has given up, not
only while it is running.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from alpha.runtime.resilience.clock import Clock, coerce_clock
from alpha.runtime.supervisor.policy import (
    RestartAction,
    RestartDecision,
    RestartLedger,
    SupervisorPolicy,
    SupervisorReason,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ChildExit",
    "ProcessSupervisor",
    "SupervisorReport",
    "SupervisorStatus",
]

#: How the process ended, as observed by the supervisor.
ChildExit = Callable[[int], SupervisorReason]

#: A bounded readiness probe. Returns True once the child is serving.
HealthCheck = Callable[[], bool | Awaitable[bool]]

#: Bound on the retained attempt history, so a long-lived supervisor's
#: diagnostics cannot grow without limit.
_HISTORY_CAP: Final[int] = 64


@dataclass(frozen=True, slots=True)
class SupervisorStatus:
    """A point-in-time read of what the supervisor is doing."""

    running: bool
    attempt: int
    action: RestartAction
    reason: str
    restarts_in_window: int
    restart_budget: int
    crash_loop: bool
    safe_mode: bool
    safe_mode_used: int
    last_exit: SupervisorReason | None
    episode: int

    def to_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "attempt": self.attempt,
            "action": self.action.value,
            "reason": self.reason,
            "restarts_in_window": self.restarts_in_window,
            "restart_budget": self.restart_budget,
            "crash_loop": self.crash_loop,
            "safe_mode": self.safe_mode,
            "safe_mode_used": self.safe_mode_used,
            "last_exit": self.last_exit.value if self.last_exit else None,
            "episode": self.episode,
        }


@dataclass(frozen=True, slots=True)
class SupervisorReport:
    """The six questions, answered together.

    Structured rather than prose on purpose: a log line, an HTTP body, and a
    support bundle should not each reword this, and a human reading a crashed
    host should not have to guess which of the six is missing.
    """

    what_failed: str
    why_it_failed: str
    where_it_failed: str
    what_alpha_was_doing: str
    what_alpha_tried: tuple[str, ...]
    what_happens_next: str
    status: SupervisorStatus = field(
        default_factory=lambda: SupervisorStatus(running=False, attempt=0, action=RestartAction.GIVE_UP, reason="", restarts_in_window=0, restart_budget=0, crash_loop=False, safe_mode=False, safe_mode_used=0, last_exit=None, episode=0)
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "what_failed": self.what_failed,
            "why_it_failed": self.why_it_failed,
            "where_it_failed": self.where_it_failed,
            "what_alpha_was_doing": self.what_alpha_was_doing,
            "what_alpha_tried": list(self.what_alpha_tried),
            "what_happens_next": self.what_happens_next,
            "status": self.status.to_dict(),
        }

    def to_text(self) -> str:
        return "\n".join(
            [
                f"WHAT FAILED:        {self.what_failed}",
                f"WHY IT FAILED:      {self.why_it_failed}",
                f"WHERE IT FAILED:    {self.where_it_failed}",
                f"ALPHA WAS DOING:    {self.what_alpha_was_doing}",
                f"ALPHA TRIED:        {'; '.join(self.what_alpha_tried) or 'nothing yet'}",
                f"WHAT HAPPENS NEXT:  {self.what_happens_next}",
            ]
        )


def _default_argv() -> tuple[str, ...]:
    """A harmless default command.

    Deliberately not Alpha's real entrypoint. The supervisor is a generic
    lifecycle owner; wiring a specific command is the host's decision (the Windows
    launcher and the service definitions own that), and defaulting to something
    that boots the backend would make a misconfigured supervisor start a second
    Gateway.
    """
    return (sys.executable, "-c", "import time; time.sleep(3600)")


class ProcessSupervisor:
    """Supervise one child process with a bounded, non-looping restart policy."""

    def __init__(
        self,
        *,
        name: str = "alpha",
        argv: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        safe_mode_argv: Sequence[str] | None = None,
        safe_mode_env: Mapping[str, str] | None = None,
        policy: SupervisorPolicy | None = None,
        clock: Clock | None = None,
        health_check: HealthCheck | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._name = name
        self._argv = tuple(argv) if argv else _default_argv()
        self._env = dict(env) if env else None
        self._cwd = cwd
        self._safe_mode_argv = tuple(safe_mode_argv) if safe_mode_argv else None
        self._safe_mode_env = dict(safe_mode_env) if safe_mode_env else None
        self._policy = policy or SupervisorPolicy()
        self._clock = coerce_clock(clock)
        self._health_check = health_check
        self._log = logger_ or logger

        self._ledger = RestartLedger(self._policy, clock=self._clock)
        self._process: subprocess.Popen[bytes] | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._spawned = asyncio.Event()
        self._started_at: float = 0.0
        self._attempt = 0
        self._last_exit: SupervisorReason | None = None
        self._safe_mode_active = False
        self._history: list[str] = []
        self._decision = RestartDecision(action=RestartAction.RESTART, reason="not started", delay_seconds=0.0, restarts_in_window=0, restart_budget=self._policy.restart_budget, crash_loop=False)
        self._stop_reason = ""

    # -- introspection -------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def ledger(self) -> RestartLedger:
        return self._ledger

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def safe_mode_active(self) -> bool:
        return self._safe_mode_active

    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    async def wait_spawned(self, *, timeout: float = 10.0) -> bool:
        """Wait until the child process exists.

        Exposed because "the child has been created" is a real boundary a host
        needs — the Windows launcher waits on it before probing health, and a
        test needs it to assert on a running child without sleeping. Returns
        False on timeout rather than raising, so a caller can report "the child
        never started" as an outcome instead of an exception.
        """
        try:
            await asyncio.wait_for(self._spawned.wait(), timeout=max(0.0, float(timeout)))
        except TimeoutError:
            return False
        return True

    def status(self) -> SupervisorStatus:
        return SupervisorStatus(
            running=self.running,
            attempt=self._attempt,
            action=self._decision.action,
            reason=self._decision.reason,
            restarts_in_window=self._decision.restarts_in_window,
            restart_budget=self._policy.restart_budget,
            crash_loop=self._decision.crash_loop,
            safe_mode=self._safe_mode_active,
            safe_mode_used=self._ledger.safe_mode_used,
            last_exit=self._last_exit,
            episode=self._ledger.episode,
        )

    def diagnostics(self) -> SupervisorReport:
        """Answer the six diagnostic questions from recorded history."""
        status = self.status()
        last = self._last_exit
        why = {
            SupervisorReason.SPAWN_FAILED: "the process could not be started at all, so the configuration or the executable is wrong",
            SupervisorReason.HEALTH_FAILED: f"the process started but never became healthy within {self._policy.health_timeout_seconds:.0f}s",
            SupervisorReason.CRASHED: "the process exited unexpectedly",
            SupervisorReason.NORMAL: "the process exited normally",
            None: "the process has not exited yet",
        }[last]
        if status.crash_loop:
            happens_next = "The supervisor has stopped restarting. Fix the cause above, then start it again; a fresh start gets a fresh budget."
        elif status.action is RestartAction.SAFE_MODE:
            happens_next = f"The supervisor will retry once with a reduced configuration (attempt {status.safe_mode_used}/{self._policy.safe_mode_attempts})."
        elif status.running:
            happens_next = "Nothing; the process is running."
        else:
            happens_next = f"The supervisor will restart after {self._decision.delay_seconds:.1f}s."
        return SupervisorReport(
            what_failed=f"The {self._name} process is not running" if not status.running else f"The {self._name} process is running",
            why_it_failed=why,
            where_it_failed=self._where_failed(),
            what_alpha_was_doing=self._what_alpha_was_doing(last),
            what_alpha_tried=tuple(self._history[-8:]),
            what_happens_next=happens_next,
            status=status,
        )

    # -- lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        """Supervise until stopped or until the policy gives up.

        The wait between attempts is ``asyncio.sleep`` on the ledger's decision,
        and it is interruptible, so a shutdown during a long backoff does not
        have to sit it out.
        """
        self._stopping = asyncio.Event()
        while not self._stopping.is_set():
            outcome = await self._attempt_once()
            if outcome is None:
                return
            decision, reason = outcome
            if not decision.should_restart:
                self._stop_reason = decision.reason
                self._log.error("supervisor %s stopped: %s", self._name, decision.reason)
                return
            if decision.action is RestartAction.SAFE_MODE:
                self._safe_mode_active = True
            if decision.delay_seconds <= 0:
                continue
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=decision.delay_seconds)
            except TimeoutError:
                continue

    async def _attempt_once(self) -> tuple[RestartDecision, SupervisorReason] | None:
        """One start/watch/record cycle. Returns None when asked to stop.

        The supervisor's own status, history and safe-mode latch are updated
        here rather than in :meth:`run`, so ``status()`` and ``diagnostics()``
        reflect the last attempt no matter who drove it: a host stepping single
        attempts, and a test asserting on one, see the same evidence the loop
        sees.
        """
        if self._stopping.is_set():
            return None
        self._attempt = self._ledger.record_start()
        started_at = self._clock.now()
        argv = self._safe_mode_argv if (self._safe_mode_active and self._safe_mode_argv) else self._argv
        env = self._safe_mode_env if (self._safe_mode_active and self._safe_mode_env is not None) else self._env

        try:
            self._process = subprocess.Popen(  # noqa: S603 - argv is operator-supplied, never shell
                list(argv),
                env=env,
                cwd=self._cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as exc:
            # Could not even start: nothing ran, so another identical attempt
            # cannot help. The ledger skips safe mode for this reason.
            self._log.error("supervisor %s could not start %s: %s", self._name, argv[:1], exc)
            self._attempt += 1
            decision = self._ledger.record_end(reason=SupervisorReason.SPAWN_FAILED, started_at=started_at)
            self._record_attempt(decision, SupervisorReason.SPAWN_FAILED)
            return decision, SupervisorReason.SPAWN_FAILED

        self._spawned.set()

        healthy = await self._await_health()
        exit_code = await self._await_exit()
        uptime = self._clock.now() - started_at

        if not healthy and exit_code == 0:
            reason = SupervisorReason.HEALTH_FAILED
            self._log.warning("supervisor %s: child exited 0 but never passed its health check", self._name)
        elif exit_code == 0:
            reason = SupervisorReason.NORMAL
        else:
            reason = SupervisorReason.CRASHED
            self._log.warning("supervisor %s: child exited %s after %.1fs", self._name, exit_code, uptime)

        self._process = None
        decision = self._ledger.record_end(reason=reason, started_at=started_at)
        self._attempt += 1
        self._record_attempt(decision, reason)
        return decision, reason

    def _record_attempt(self, decision: RestartDecision, reason: SupervisorReason) -> None:
        """Publish one attempt's outcome onto the supervisor's own state."""
        self._decision = decision
        self._last_exit = reason
        self._history.append(f"attempt {self._attempt}: {reason.value} -> {decision.action.value}")
        if len(self._history) > _HISTORY_CAP:
            del self._history[: len(self._history) - _HISTORY_CAP]
        if self._safe_mode_active and decision.action is not RestartAction.RESTART:
            # Leaving the reduced configuration requires evidence, so the latch
            # is released only when the supervisor is about to do something other
            # than keep restarting inside it.
            self._safe_mode_active = False

    async def _await_health(self) -> bool:
        """Wait up to ``health_timeout_seconds`` for the readiness probe.

        Bounded on :func:`time.monotonic`, **not** on the injected clock. The
        injected clock is for policy decisions the test wants to control; this
        loop is a real-time bound on an external process, and measuring it with
        a ``ManualClock`` — which only moves when a test says so — would spin
        forever instead of timing out. Policy timing stays on the injected clock.
        """
        if self._health_check is None:
            return True
        deadline = time.monotonic() + self._policy.health_timeout_seconds
        interval = max(0.01, min(0.05, self._policy.health_timeout_seconds / 10.0))
        while time.monotonic() < deadline:
            if self._stopping.is_set():
                return True
            try:
                result = self._health_check()
                if hasattr(result, "__await__"):
                    result = await result  # type: ignore[assignment]
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.debug("supervisor %s health check raised", self._name, exc_info=True)
                result = False
            if result:
                return True
            await asyncio.sleep(interval)
        return False

    async def _await_exit(self) -> int:
        """Block until the child exits, honouring a stop request.

        Polled rather than awaited on ``proc.wait()`` so a stop request is
        observed promptly and a supervisor can never be wedged by a child that
        ignores its signals. The poll interval is short because the only thing
        being awaited here is a process exit, not work.
        """
        process = self._process
        if process is None:  # pragma: no cover - defensive
            return 0
        while True:
            code = process.poll()
            if code is not None:
                return int(code)
            if self._stopping.is_set():
                self.terminate()
            await asyncio.sleep(0.05)

    def start(self) -> asyncio.Task[None]:
        """Start supervising as a background task; idempotent."""
        if self._task is not None and not self._task.done():
            return self._task
        self._stopping = asyncio.Event()
        self._spawned = asyncio.Event()
        self._task = asyncio.create_task(self.run(), name=f"alpha-supervisor-{self._name}")
        return self._task

    def stop(self, *, reason: str = "supervisor stop requested") -> None:
        """Ask the supervisor to stop; does not block."""
        self._stop_reason = reason
        if self._stopping is not None:
            self._stopping.set()
        self.terminate()

    async def wait(self, *, timeout: float | None = None) -> None:
        """Wait for the supervision loop to finish."""
        task = self._task
        if task is None:
            return
        if timeout is None:
            await task
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    def terminate(self, *, timeout: float = 5.0) -> None:
        """Stop the child, escalating to a kill if it ignores the request.

        Windows has no ``SIGTERM`` graceful path, so a terminate/kill ladder is
        the portable shape rather than a platform special case.
        """
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._log.warning("supervisor %s: child ignored terminate, killing", self._name)
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                process.wait(timeout=timeout)

    # -- internals -----------------------------------------------------------

    def _where_failed(self) -> str:
        if self._process is not None:
            return f"process pid {self._process.pid}"
        argv = self._safe_mode_argv if self._safe_mode_active and self._safe_mode_argv else self._argv
        if not argv:
            return "no command configured"
        executable = argv[0]
        if not _command_resolvable(executable):
            return f"executable {executable!r} was not found on PATH"
        return f"command {executable!r}, cwd {self._cwd or os.getcwd()!r}"

    def _what_alpha_was_doing(self, last: SupervisorReason | None) -> str:
        if self._safe_mode_active:
            return f"running {self._name} in reduced configuration (safe mode), attempt {self._attempt}"
        if last is SupervisorReason.HEALTH_FAILED:
            return f"waiting for {self._name} to answer its health check after starting it"
        if last is None:
            return f"about to start {self._name}"
        return f"running {self._name}"


def _command_resolvable(executable: str) -> bool:
    """True when *executable* is an existing path or a command on PATH."""
    if os.path.isabs(executable) or os.sep in executable or (os.altsep and os.altsep in executable):
        return os.path.exists(executable)
    return shutil.which(executable) is not None
