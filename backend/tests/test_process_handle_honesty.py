"""process_handle must never report a success it has not verified.

Before the fix, ``action='start'`` returned the literal string "Process started
in background." unconditionally -- for a command that had already exited 1, and
for a command whose binary did not exist at all. Two further lies lived in the
same surface:

* ``ProcessHandle.kill()`` returned True whenever ``terminate()``/``kill()``
  did not raise (never observing the process) and stamped a fabricated exit
  code of ``-9`` on the handle, which ``poll`` and ``list`` then published as
  the process's real status;
* ``ProcessManager.start_background`` let a spawn failure escape as a raw
  exception with no handle, so nothing could report the failure.

The commands used here are shell builtins (``exit``, ``echo``) so a terminal
state is reached in milliseconds and the assertions do not depend on how long
this host takes to boot a Python interpreter.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from alpha.sandbox.process_manager import (
    START_SETTLE_SECONDS,
    ProcessManager,
    ProcessSpawnError,
    get_process_manager,
)
from alpha.tools.builtins.process_handle_tool import process_handle_tool


def _start(command: str) -> str:
    return process_handle_tool.invoke({"action": "start", "command": command})


def _handle_id(start_out: str) -> str:
    for line in start_out.splitlines():
        if line.startswith("Handle ID:"):
            return line.split("Handle ID:", 1)[1].strip()
    raise AssertionError(f"no handle id in start output: {start_out!r}")


def _wait_terminal(handle_id: str, timeout: float = 60.0) -> str:
    """Poll until the process reports a terminal state, then return the report.

    A bounded wait for the real terminal state keeps the assertion about the
    tool's output and removes the fixed-sleep machine-speed assumption.
    """
    deadline = time.monotonic() + timeout
    out = process_handle_tool.invoke({"action": "poll", "handle_id": handle_id})
    while "still RUNNING" in out and time.monotonic() < deadline:
        time.sleep(0.05)
        out = process_handle_tool.invoke({"action": "poll", "handle_id": handle_id})
    return out


_SUCCESS_PHRASES = (
    "Process started in background",
    "completed successfully",
    "Successfully",
)


# ---------------------------------------------------------------------------
# A command that fails immediately must not read as success
# ---------------------------------------------------------------------------


def test_immediate_non_zero_exit_is_not_a_success() -> None:
    start_out = _start("exit 3")
    assert "completed successfully" not in start_out, start_out
    assert "Process started in background" not in start_out, start_out
    assert "FAILED" in start_out, start_out
    assert "exit code 3" in start_out, start_out

    # ...and the real exit status is retrievable from the handle.
    poll_out = _wait_terminal(_handle_id(start_out))
    assert "TERMINATED with exit code 3" in poll_out, poll_out
    assert "FAILED" in poll_out, poll_out


def test_nonexistent_binary_is_not_a_success() -> None:
    # The shell needs a moment to report "not recognized", so whether the
    # failure is already observable inside the start settle window is a
    # machine-speed question. What must hold unconditionally is that start
    # never claims success, and that the real non-zero status is retrievable.
    start_out = _start("definitely_not_a_real_binary_zzz --go")
    assert "completed successfully" not in start_out, start_out
    assert "Process started in background" not in start_out, start_out

    handle_id = _handle_id(start_out)
    poll_out = _wait_terminal(handle_id)
    assert "TERMINATED with exit code" in poll_out, poll_out
    assert "exit code 0" not in poll_out, poll_out
    assert "non-zero" in poll_out, poll_out
    assert "FAILED" in poll_out, poll_out
    # The real reason is published, not swallowed.
    assert "not recognized" in poll_out or "not found" in poll_out, poll_out


def test_failing_command_output_is_published_not_dropped() -> None:
    # stderr is merged into stdout; a failure must carry its evidence.
    start_out = _start(f'"{sys.executable}" -c "import sys; sys.stderr.write(\'boom\\n\'); sys.exit(2)"')
    handle_id = _handle_id(start_out)
    tail_out = process_handle_tool.invoke({"action": "tail", "handle_id": handle_id})
    deadline = time.monotonic() + 60.0
    while "boom" not in tail_out and time.monotonic() < deadline:
        time.sleep(0.1)
        tail_out = process_handle_tool.invoke({"action": "tail", "handle_id": handle_id})
    assert "boom" in tail_out, tail_out
    poll_out = _wait_terminal(handle_id)
    assert "exit code 2" in poll_out, poll_out
    assert "boom" in poll_out, poll_out


# ---------------------------------------------------------------------------
# The inverse: a command that genuinely succeeds is still reported as success
# ---------------------------------------------------------------------------


def test_genuine_success_is_still_reported_as_success() -> None:
    start_out = _start("exit 0")
    assert "completed successfully" in start_out, start_out
    assert "exit code 0" in start_out, start_out
    assert "FAILED" not in start_out, start_out

    poll_out = _wait_terminal(_handle_id(start_out))
    assert "TERMINATED with exit code 0" in poll_out, poll_out
    assert "success" in poll_out, poll_out


def test_genuine_success_with_output_is_still_success() -> None:
    start_out = _start(f'"{sys.executable}" -c "print(\'tool_test_ok\')"')
    handle_id = _handle_id(start_out)
    assert "FAILED" not in start_out, start_out

    poll_out = _wait_terminal(handle_id)
    assert "TERMINATED with exit code 0" in poll_out, poll_out
    tail_out = process_handle_tool.invoke({"action": "tail", "handle_id": handle_id})
    assert "tool_test_ok" in tail_out, tail_out


# ---------------------------------------------------------------------------
# Still-running is honest, not a success claim
# ---------------------------------------------------------------------------


def test_long_running_command_reports_running_not_success() -> None:
    start_out = _start(f'"{sys.executable}" -c "import time; time.sleep(120)"')
    assert "FAILED" not in start_out, start_out
    assert "completed successfully" not in start_out, start_out
    assert "RUNNING" in start_out, start_out
    # The claim is explicitly evidence-backed, not asserted.
    assert "spawn verified" in start_out, start_out
    assert f"after {START_SETTLE_SECONDS:g}s" in start_out, start_out

    handle_id = _handle_id(start_out)
    out = process_handle_tool.invoke({"action": "kill", "handle_id": handle_id})
    assert "Successfully terminated" in out, out
    # The kill is verified, and the reported status is the OS-reported one
    # (never a fabricated -9).
    assert "death verified" in out, out
    assert "-9" not in out, out


def test_still_running_does_not_claim_an_exit_code() -> None:
    out = _start(f'"{sys.executable}" -c "import time; time.sleep(120)"')
    assert "exit status not yet known" in out, out
    handle_id = _handle_id(out)
    process_handle_tool.invoke({"action": "kill", "handle_id": handle_id})


# ---------------------------------------------------------------------------
# Spawn failure is a failure, not an unhandled exception
# ---------------------------------------------------------------------------


def test_spawn_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("no shell available")

    monkeypatch.setattr("alpha.sandbox.process_manager.subprocess.Popen", boom)
    out = _start("anything at all")
    assert out.startswith("Error:"), out
    assert "failed to start background process" in out, out
    assert "no shell available" in out, out
    for phrase in _SUCCESS_PHRASES:
        assert phrase not in out, out


def test_start_background_raises_spawn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("no shell available")

    monkeypatch.setattr("alpha.sandbox.process_manager.subprocess.Popen", boom)
    with pytest.raises(ProcessSpawnError):
        ProcessManager().start_background("anything at all")


# ---------------------------------------------------------------------------
# kill() reports the real status, never a fabricated one
# ---------------------------------------------------------------------------


def test_kill_does_not_fabricate_a_minus_nine_exit_code() -> None:
    pm = ProcessManager()
    handle = pm.start_background(f'"{sys.executable}" -c "import time; time.sleep(120)"')
    assert handle.kill() is True
    code = handle.poll()
    assert code is not None
    assert code != -9, f"kill() fabricated the exit status: {code}"
    assert not handle.is_running()


def test_kill_reports_failure_when_the_process_survives() -> None:
    from alpha.sandbox.process_manager import ProcessHandle

    class _Survivor:
        """A process that accepts every kill and stays alive."""

        pid = 4242
        returncode = None
        stdout = None

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("cmd", timeout or 0)

    victim = ProcessHandle(handle_id="proc_fake", command="fake", process=_Survivor(), cwd=".")
    assert victim.kill() is False, "kill() must not report success for a process that is still alive"


# ---------------------------------------------------------------------------
# list / tail are consistent with poll
# ---------------------------------------------------------------------------


def test_list_and_poll_agree_on_exit_status() -> None:
    start_out = _start("exit 4")
    handle_id = _handle_id(start_out)
    poll_out = _wait_terminal(handle_id)
    assert "TERMINATED with exit code 4" in poll_out, poll_out

    list_out = process_handle_tool.invoke({"action": "list"})
    line = next(row for row in list_out.splitlines() if handle_id in row)
    assert "EXITED(4)" in line, line
    assert "RUNNING" not in line, line


def test_poll_on_unknown_handle_is_an_error_not_a_zero() -> None:
    out = process_handle_tool.invoke({"action": "poll", "handle_id": "proc_does_not_exist"})
    assert out.startswith("Error:"), out
    assert "exit code 0" not in out, out


def test_global_manager_is_the_one_the_tool_uses() -> None:
    assert get_process_manager() is get_process_manager()
    start_out = _start("exit 0")
    handle_id = _handle_id(start_out)
    assert get_process_manager().get(handle_id) is not None
