"""Regression anchor: a REPL cell must never block the event loop.

Agent-authored cells are arbitrary synchronous Python. Executed inline on the
event loop, a single sleeping loop or a hung ``bash(...)`` froze every other
coroutine in the process, and the ``asyncio.wait_for`` deadline could not fire
either -- a timeout callback is not scheduled while the loop sits inside
``exec``. All of that is pinned here:

* a cell that outlives its deadline is interrupted, and the caller gets a typed
  :class:`ReplTimeoutError` *while the cell is still running*;
* a blocked cell in one session does not stall a concurrent run in another, and
  the loop keeps scheduling work while it is blocked;
* a shell subprocess a cell started is genuinely *killed* when the cell's
  deadline expires, rather than being left running and merely abandoned.

These tests live under ``tests/blocking_io/`` so the strict Blockbuster gate is
active for them: if the cell body ever moved back onto the loop, ``time.sleep``
inside it would raise ``BlockingError`` before any timing assertion could pass.

Two deliberate choices keep the assertions honest rather than machine-dependent:

* the blocking cells park on an event the test releases at the end, so they are
  released by the *test* and can never "finish on their own" and mask the
  regression;
* the primary evidence is structural -- ``finished.is_set()`` is False when the
  caller is released, and the concurrent run completes while the other session's
  cell is provably still blocked. Wall-clock bounds are secondary and sized
  against the cell's own blocking budget, so they still fail an implementation
  that waits for the cell even on a heavily loaded machine.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
import pytest_asyncio
from langgraph.prebuilt import ToolRuntime

from alpha.sandbox.repl.session import ReplSession, ReplTimeoutError, get_repl_session
from alpha.tools.builtins.python_repl_tool import python_repl_tool

pytestmark = pytest.mark.asyncio

#: How long a blocking cell is willing to block. A cell that is *not* interrupted
#: holds its caller for this long, which is what the wall-clock bounds below are
#: sized against. No test waits this long: the test releases the cell.
_BLOCK_FOR_MAX_SECONDS = 120

#: The hanging shell child sleeps for this long. Only the kill can end it, and it
#: must outlast every deadline involved by a wide margin.
_SHELL_CHILD_SLEEP_SECONDS = 120


def _blocking_cell_source() -> str:
    """A cell that blocks until ``release`` is set (or for the full budget).

    Sets ``finished`` on the way out -- including via ``finally`` -- so a test can
    tell "still running" from "already done" without relying on a clock.
    """
    return (
        "import time\n"
        f"end = time.monotonic() + {_BLOCK_FOR_MAX_SECONDS}\n"
        "try:\n"
        "    while time.monotonic() < end and not release.is_set():\n"
        "        time.sleep(0.05)\n"
        "finally:\n"
        "    finished.set()\n"
        "'released'\n"
    )


def _hanging_shell_command(pid_file: Path) -> str:
    """A shell command that publishes its PID, then sleeps far past any deadline."""
    script = f"import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep({_SHELL_CHILD_SLEEP_SECONDS})"
    argv = [sys.executable, "-c", script, str(pid_file)]
    join = subprocess.list2cmdline if os.name == "nt" else shlex.join
    return join(argv)


def _process_is_alive(pid: int) -> bool:
    """True while *pid* names a live process (never signals it, unlike ``os.kill``)."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            # WAIT_TIMEOUT (0x102) means the process is still running.
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_until(predicate: Callable[[], object], *, timeout: float) -> object:
    """Poll *predicate* until truthy or *timeout* elapses; returns the value."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            return value
        await asyncio.sleep(0.02)


@pytest.fixture()
def cell_gate() -> dict[str, threading.Event]:
    """The two events a blocking cell uses: ``release`` and ``finished``."""
    return {"release": threading.Event(), "finished": threading.Event()}


def _arm(session: ReplSession, gate: dict[str, threading.Event]) -> None:
    session.namespace["release"] = gate["release"]
    session.namespace["finished"] = gate["finished"]


@pytest_asyncio.fixture()
async def sessions(tmp_path) -> dict[str, ReplSession]:
    """Build every session these tests use, and warm the worker-thread path.

    Sessions are built eagerly (setup, no event loop running) because
    construction touches the filesystem, which the strict gate rightly refuses
    on the loop. Ids are unique per test, so each test gets fresh sessions.

    The warm-up run matters: the *first* ``Thread.start()`` in a process can take
    a very long time on a loaded machine (image paging, AV scanning, whatever
    else is competing for the CPU) and every later one is fast. Paying it here,
    through the public API, keeps the assertions below about the REPL's own
    behaviour rather than about the machine -- the structural assertions carry
    the regression either way.
    """
    warm = get_repl_session("offloop-warmup", working_dir=tmp_path)
    assert (await warm.execute("'warm'", timeout=_BLOCK_FOR_MAX_SECONDS)).result == "warm"
    return {
        "sleeping": get_repl_session("offloop-sleeping-cell", working_dir=tmp_path),
        "slow": get_repl_session("offloop-slow", working_dir=tmp_path),
        "fast": get_repl_session("offloop-fast", working_dir=tmp_path),
        "noisy": get_repl_session("offloop-noisy", working_dir=tmp_path),
        "quiet": get_repl_session("offloop-quiet", working_dir=tmp_path),
        "cell_shell": get_repl_session("offloop-cell-kills-shell", working_dir=tmp_path, shell_timeout=60.0),
        "bash_deadline": get_repl_session("offloop-bash-deadline", working_dir=tmp_path, shell_timeout=3.0),
        "fast_bash": get_repl_session("offloop-fast-bash", working_dir=tmp_path),
        "awaitable": get_repl_session("offloop-awaitable", working_dir=tmp_path),
        "tool": get_repl_session("offloop-tool-timeout", working_dir=tmp_path, shell_timeout=60.0),
    }


async def test_blocked_cell_is_interrupted_with_a_typed_timeout(sessions, cell_gate):
    """A cell past its deadline is interrupted while it is still running."""
    session = sessions["sleeping"]
    _arm(session, cell_gate)
    started = time.monotonic()

    try:
        with pytest.raises(ReplTimeoutError) as excinfo:
            await session.execute(_blocking_cell_source(), timeout=0.5)

        elapsed = time.monotonic() - started
        # The cell is still blocked, so this cannot be a cell that ran to
        # completion: the caller was released at its own deadline.
        assert not cell_gate["finished"].is_set(), "the cell had already finished when the timeout was reported"
        assert isinstance(excinfo.value, TimeoutError)
        assert excinfo.value.timeout == 0.5
        assert "0.5s deadline" in str(excinfo.value)
        # An uninterruptible cell would hold the caller for its full
        # two-minute blocking budget.
        assert elapsed < _BLOCK_FOR_MAX_SECONDS / 2, f"the caller waited {elapsed:.1f}s for a cell it should have abandoned"
    finally:
        cell_gate["release"].set()

    # The abandoned cell unwound, so the session is usable again.
    recovered = await session.execute("'recovered'", timeout=_BLOCK_FOR_MAX_SECONDS)
    assert recovered.status == "ok"
    assert recovered.result == "recovered"

    # And the rest of the process never stopped working.
    other = sessions["fast"]
    assert (await other.execute("'still-alive'", timeout=_BLOCK_FOR_MAX_SECONDS)).result == "still-alive"


async def test_concurrent_run_is_not_blocked_by_a_blocked_cell(sessions, cell_gate):
    """A blocked cell must not delay a concurrent run, nor stall the loop."""
    slow = sessions["slow"]
    fast = sessions["fast"]
    _arm(slow, cell_gate)

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    ticks_before = ticks
    # A long deadline: the cell must stay blocked for the whole test, so that
    # "the fast run finished first" is a fact about the loop and not about luck.
    slow_task = asyncio.create_task(slow.execute(_blocking_cell_source(), timeout=_BLOCK_FOR_MAX_SECONDS))
    await asyncio.sleep(0.3)

    fast_started = time.monotonic()
    fast_result = await fast.execute("40 + 2", timeout=_BLOCK_FOR_MAX_SECONDS)
    fast_elapsed = time.monotonic() - fast_started

    try:
        # Structural, not just wall-clock: the fast run completed while the other
        # session's cell was provably still blocked. With the cell body on the
        # event loop the blocked cell *is* the loop, so this is unreachable.
        assert not cell_gate["finished"].is_set()
        assert not slow_task.done(), "the concurrent run only completed after the blocked cell finished"
        assert fast_result.status == "ok"
        assert fast_result.result == 42
        assert fast_elapsed < _BLOCK_FOR_MAX_SECONDS / 2, f"a concurrent run waited {fast_elapsed:.1f}s behind another session's blocked cell"
        # A blocked cell at a 10ms heartbeat cadence is hundreds of ticks: a loop
        # stuck inside the cell would have run almost none of them.
        assert ticks > ticks_before + 10, f"the event loop only ran {ticks - ticks_before} callbacks while a cell was blocked"
    finally:
        pulse.cancel()
        cell_gate["release"].set()

    slow_result = await slow_task
    assert slow_result.status == "ok"
    assert slow_result.result == "released"
    assert cell_gate["finished"].is_set()


async def test_concurrent_cells_capture_their_own_output(sessions, cell_gate):
    """Overlapping cells must not cross-contaminate each other's stdout."""
    noisy = sessions["noisy"]
    quiet = sessions["quiet"]
    _arm(noisy, cell_gate)

    slow_task = asyncio.create_task(
        noisy.execute("import time\nprint('noisy-start')\nwhile not release.is_set():\n    time.sleep(0.02)\nprint('noisy-end')", timeout=_BLOCK_FOR_MAX_SECONDS)
    )
    await asyncio.sleep(0.3)

    try:
        quiet_result = await quiet.execute("print('quiet-only')", timeout=_BLOCK_FOR_MAX_SECONDS)
    finally:
        cell_gate["release"].set()
    noisy_result = await slow_task

    assert quiet_result.status == "ok"
    assert "quiet-only" in quiet_result.stdout
    assert noisy_result.status == "ok"
    assert "noisy-start" in noisy_result.stdout
    assert "noisy-end" in noisy_result.stdout
    assert "quiet-only" not in noisy_result.stdout


async def test_shell_started_by_an_expired_cell_is_killed(sessions, tmp_path):
    """The cell deadline kills the shell it started -- no orphan survives."""
    # The shell's own deadline (60s) is far beyond the cell's, so only the
    # cell-deadline abort path can account for the process dying.
    session = sessions["cell_shell"]
    command = _hanging_shell_command(tmp_path / "unused.pid")
    seen: list[int] = []

    async def watch() -> None:
        while True:
            seen.extend(process.pid for process in session.live_shell_processes() if process.pid not in seen)
            await asyncio.sleep(0.02)

    watcher = asyncio.create_task(watch())
    try:
        with pytest.raises(ReplTimeoutError):
            await session.execute(f"bash({command!r})", timeout=3.0)
    finally:
        watcher.cancel()

    assert seen, "the cell never started a shell process it then timed out on"
    pid = seen[0]
    assert await _wait_until(lambda: not _process_is_alive(pid), timeout=30.0), f"shell process {pid} outlived the cell deadline"
    # And the session does not keep the reaped process registered forever.
    assert await _wait_until(lambda: not session.live_shell_processes(), timeout=30.0)


async def test_bash_deadline_kills_the_command_and_reports_a_typed_failure(sessions, tmp_path):
    """A single ``bash(...)`` is bounded on its own, and the kill is real."""
    pid_file = tmp_path / "bash-shell.pid"
    session = sessions["bash_deadline"]
    command = _hanging_shell_command(pid_file)

    result = await session.execute(f"bash({command!r})", timeout=_BLOCK_FOR_MAX_SECONDS)

    assert result.status == "error"
    assert result.error_name == "ReplTimeoutError"
    assert "3.0s deadline" in str(result.error_value)
    published = await _wait_until(lambda: pid_file.read_text(encoding="utf-8").strip() if pid_file.exists() else "", timeout=30.0)
    assert published, "the shell never published its PID"
    pid = int(str(published))
    assert not _process_is_alive(pid), f"shell process {pid} survived the bash deadline"


async def test_fast_shell_command_still_returns_its_output(sessions):
    """The bounded shell keeps working for the ordinary case."""
    session = sessions["fast_bash"]
    result = await session.execute("bash('echo hello-from-bash').stdout", timeout=_BLOCK_FOR_MAX_SECONDS)

    assert result.status == "ok"
    assert "hello-from-bash" in result.result


async def test_trailing_coroutine_is_awaited_and_bound(sessions):
    """A trailing coroutine still resolves on the loop and binds to ``_``."""
    session = sessions["awaitable"]
    result = await session.execute(
        "import asyncio\nasync def later():\n    await asyncio.sleep(0.01)\n    return 7\nlater()",
        timeout=_BLOCK_FOR_MAX_SECONDS,
    )

    assert result.status == "ok"
    assert result.result == 7
    assert result.result_repr == "7"
    assert session.namespace["_"] == 7


async def test_tool_reports_the_timeout_instead_of_hanging(sessions, cell_gate):
    """The tool surface turns the typed timeout into an agent-readable error."""
    session = sessions["tool"]
    _arm(session, cell_gate)
    runtime = ToolRuntime(
        state={},
        context={"thread_id": session.session_id},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="tool-call-offloop",
        store=None,
    )

    started = time.monotonic()
    try:
        output = await python_repl_tool.ainvoke(
            {
                "code": _blocking_cell_source(),
                "timeout": 0.5,
                "runtime": runtime,
            }
        )
    finally:
        cell_gate["release"].set()

    assert time.monotonic() - started < _BLOCK_FOR_MAX_SECONDS / 2
    assert not cell_gate["finished"].is_set(), "the tool reported a timeout only after the cell had finished"
    assert output.startswith("Error: Python execution timed out after 0.5 seconds")
