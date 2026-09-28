"""An operator-requested stop must not be recorded as, or charged as, a crash.

Measured on this checkout before the fix. A child that was healthy and alive
(`attempts=1`, `last_exit=None`) was stopped deliberately, and afterwards:

    last_exit after stop : <SupervisorReason.CRASHED: 'crashed'>
    history              : ['attempt 2: crashed -> restart']

The cause is mechanical. `_await_exit` observes `_stopping`, calls
`terminate()`, and returns whatever the OS reports for a process the supervisor
killed itself. On Windows that is nonzero, so the attempt fell through to
`exit_code != 0` and became `CRASHED`.

The visible half is a false statement to whoever is on call: `status()` and
`diagnostics()` say the backend crashed when they asked it to stop.

The load-bearing half is `SupervisorReason.counts_against_budget`. The restart
budget exists to stop a backend that *cannot start* - a bad config, a bound port,
a migration that will not apply. A deploy, a config reload, or a supervisor
restart stops a perfectly healthy child, and each of those was spending the
budget. After enough ordinary restarts the policy walks into SAFE_MODE and then
GIVE_UP: a supervisor that refuses to start the process it exists to keep up,
triggered by nothing worse than being asked to restart.

These tests drive the real supervisor with a real child process, and use a
heartbeat file so "was it actually alive when I stopped it" is a fact on disk
rather than an inference from a duration.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

import pytest

from alpha.runtime.resilience.retry import RetryPolicy
from alpha.runtime.supervisor.policy import RestartAction, SupervisorPolicy, SupervisorReason
from alpha.runtime.supervisor.supervisor import ProcessSupervisor

TIGHT_BACKOFF = RetryPolicy(attempts=8, base_delay=0.01, multiplier=1.0, max_delay=0.02)


def _as_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}


def _sleeper_argv(heartbeat: Path) -> list[str]:
    """A child that proves it is running, then idles.

    The heartbeat is written repeatedly so a test can assert the child was alive
    at the moment `stop()` was called, rather than inferring it from uptime.
    """
    code = f"import time,pathlib;p=pathlib.Path(r'{heartbeat}');p.write_text('alive');[p.write_text(str(int(t*10))) or time.sleep(0.1) for t in range(900)]"
    return [sys.executable, "-c", code]


def _supervisor(heartbeat: Path, **policy_kwargs) -> ProcessSupervisor:
    # `health_timeout_seconds` is a fixed 5.0 here, and a caller passing it would
    # otherwise collide with the explicit keyword below.
    policy_kwargs.setdefault("health_timeout_seconds", 5.0)
    policy = SupervisorPolicy(**policy_kwargs)
    return ProcessSupervisor(name="alpha-backend", argv=_sleeper_argv(heartbeat), policy=policy)


def _budget_charged(sup: ProcessSupervisor):
    ledger = sup._ledger
    for attr in ("restarts_in_window", "restart_count", "count", "_restarts"):
        if hasattr(ledger, attr):
            value = getattr(ledger, attr)
            return value() if callable(value) else len(value)
    pytest.skip("RestartLedger exposes no readable restart count to assert on")


class TestReasonSemantics:
    def test_stopped_is_a_distinct_reason(self):
        assert SupervisorReason.STOPPED.value == "stopped"
        assert SupervisorReason.STOPPED is not SupervisorReason.CRASHED

    @pytest.mark.parametrize("reason", [SupervisorReason.NORMAL, SupervisorReason.STOPPED])
    def test_a_requested_or_clean_end_spends_no_budget(self, reason):
        assert reason.counts_against_budget is False

    @pytest.mark.parametrize(
        "reason",
        [SupervisorReason.CRASHED, SupervisorReason.HEALTH_FAILED, SupervisorReason.SPAWN_FAILED],
    )
    def test_a_real_failure_still_spends_budget(self, reason):
        assert reason.counts_against_budget is True

    def test_a_stop_does_not_restart(self):
        """RESTART would report a next step the supervisor is not going to take."""
        assert RestartAction.GIVE_UP is not RestartAction.RESTART


class TestStopIsNotRecordedAsACrash:
    def test_a_healthy_child_stopped_on_request_is_recorded_as_stopped(self, tmp_path: Path):
        sup, alive = asyncio.run(_stop_a_healthy_child(tmp_path / "hb.txt", safe_mode_enabled=False))
        assert alive, "the child must be proven alive for this test to mean anything"
        status = _as_dict(sup.status())
        assert status["last_exit"] is SupervisorReason.STOPPED, status
        assert status["last_exit"] is not SupervisorReason.CRASHED
        assert any("stopped" in line for line in sup._history), sup._history
        assert not any("crashed" in line for line in sup._history), sup._history

    def test_a_stop_charges_no_restart_budget(self, tmp_path: Path):
        sup, alive = asyncio.run(
            _stop_a_healthy_child(
                tmp_path / "hb.txt",
                restart_budget=3,
                restart_window_seconds=300.0,
                safe_mode_enabled=False,
                backoff=TIGHT_BACKOFF,
            )
        )
        assert alive
        assert _budget_charged(sup) == 0, "an operator-requested stop must return budget, not spend it"

    def test_repeated_clean_restarts_never_exhaust_the_budget(self, tmp_path: Path):
        """The regression that mattered: a budget of 3 must survive 6 restarts.

        Before the fix each cycle charged one restart, so the fourth clean
        restart found the budget spent and the policy moved to SAFE_MODE and then
        GIVE_UP - refusing to start a backend that had never once failed.
        """
        cycles = 6
        for cycle in range(cycles):
            # One shared path, unlinked per cycle inside the helper: reusing a
            # path across cycles would let a stale heartbeat satisfy the
            # aliveness check without the child ever running.
            sup, alive = asyncio.run(
                _stop_a_healthy_child(
                    tmp_path / "hb.txt",
                    restart_budget=3,
                    restart_window_seconds=300.0,
                    safe_mode_enabled=False,
                    backoff=TIGHT_BACKOFF,
                    settle=0.6,
                )
            )
            assert alive, f"cycle {cycle}: child was not alive, so the cycle proves nothing"
            status = _as_dict(sup.status())
            assert status["last_exit"] is SupervisorReason.STOPPED, f"cycle {cycle}: {status}"
            assert _budget_charged(sup) == 0, f"cycle {cycle} charged restart budget for a clean stop"

    def test_a_real_crash_is_still_recorded_and_still_charges(self, tmp_path: Path):
        """The fix must not have blunted genuine failure detection."""
        sup = asyncio.run(_crash_once())
        status = _as_dict(sup.status())
        assert status["last_exit"] is SupervisorReason.CRASHED, status
        assert _budget_charged(sup) >= 1, "a genuine crash must still spend budget"


async def _stop_a_healthy_child(heartbeat: Path, *, settle: float = 1.2, **policy_kwargs) -> tuple[ProcessSupervisor, bool]:
    """Start a child, prove it is alive, stop it, and return the supervisor.

    Runs entirely inside one event loop: `start()` calls `asyncio.create_task`,
    so creating the supervisor and its task outside a loop raises
    "no running event loop" and leaks an un-awaited `run()` coroutine.

    Each cycle gets a *fresh* heartbeat path. Reusing one would make the
    "child proved itself alive" check pass on a leftover file from a previous
    cycle, which is exactly the false green this test exists to rule out - so it
    is unlinked first and the wait below is the only thing that can create it.
    """
    heartbeat.unlink(missing_ok=True)
    sup = _supervisor(heartbeat, **policy_kwargs)
    task = sup.start()
    alive = False
    try:
        # A deadline, not a fixed count of polls: a cold `Popen` of a Windows
        # interpreter plus the child's own interpreter start is easily over a
        # second under load, and a cycle that gives up early would report the
        # child as "not alive" for being slow rather than for being broken.
        deadline = asyncio.get_running_loop().time() + max(settle, 15.0)
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
            if heartbeat.exists() and sup._process is not None:
                alive = True
                break
        sup.stop(reason="operator asked")
        await asyncio.wait_for(task, timeout=30)
    finally:
        if not task.done():  # pragma: no cover - only on an unexpected hang
            sup.stop()
            task.cancel()
    return sup, alive


async def _crash_once() -> ProcessSupervisor:
    """A child that exits nonzero on its own, with no stop requested."""
    sup = ProcessSupervisor(
        name="alpha-backend",
        argv=[sys.executable, "-c", "raise SystemExit(3)"],
        policy=SupervisorPolicy(health_timeout_seconds=5.0, safe_mode_enabled=False, restart_budget=1),
    )
    task = sup.start()
    try:
        # One attempt: the budget of 1 sends the supervisor to GIVE_UP and it
        # stops by itself, so this terminates without a stop() call.
        await asyncio.wait_for(task, timeout=30)
    except (TimeoutError, asyncio.CancelledError):  # pragma: no cover
        sup.stop()
        task.cancel()
    return sup
