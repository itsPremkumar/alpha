"""Unit tests for background process manager and handle tracking."""

import sys
import time

from alpha.sandbox.process_manager import ProcessManager
from alpha.tools.builtins.process_handle_tool import process_handle_tool


def test_process_manager_lifecycle():
    pm = ProcessManager()

    # Launch a quick command that echoes text and sleeps shortly
    cmd = f'"{sys.executable}" -c "import time; print(\'line1\'); print(\'line2\'); time.sleep(0.2); print(\'done\')"'
    handle = pm.start_background(cmd)

    assert handle.handle_id.startswith("proc_")
    assert handle.pid > 0

    # Wait for process to complete. A fixed sleep is a machine-speed assumption
    # (a cold interpreter start can outlast it on a loaded host), so wait for the
    # real terminal state with a bounded deadline instead.
    deadline = time.monotonic() + 30.0
    while handle.is_running() and time.monotonic() < deadline:
        time.sleep(0.1)

    exit_code = handle.poll()
    assert exit_code == 0
    assert not handle.is_running()

    output = handle.output()
    assert "line1" in output
    assert "line2" in output
    assert "done" in output

    tail = handle.tail(lines=2)
    assert "done" in tail

    # Check exit notices
    notices = pm.check_exit_notices()
    assert len(notices) >= 1
    matching = [n for n in notices if n["handle_id"] == handle.handle_id]
    assert len(matching) == 1
    assert matching[0]["exit_code"] == 0


def test_process_termination():
    pm = ProcessManager()

    cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    handle = pm.start_background(cmd)

    assert handle.is_running()
    killed = handle.kill()
    assert killed is True
    assert not handle.is_running()


def _wait_for_terminal_poll(handle_id: str, *, timeout: float = 30.0) -> str:
    """Poll the tool until the process reports a terminal state.

    The original test slept a fixed 0.4s and polled once, which is a machine-speed
    assumption: a cold interpreter start on a loaded Windows host takes longer
    than that, so the poll correctly reported "still RUNNING" and the test failed
    for reasons unrelated to the code under test. Bounded polling keeps the real
    assertion (the process MUST terminate with exit code 0) and only removes the
    timing guesswork.
    """
    deadline = time.monotonic() + timeout
    poll_out = ""
    while True:
        poll_out = process_handle_tool.invoke({
            "action": "poll",
            "handle_id": handle_id,
        })
        if "still RUNNING" not in poll_out or time.monotonic() >= deadline:
            return poll_out
        time.sleep(0.1)


def test_process_handle_tool():
    cmd = f'"{sys.executable}" -c "print(\'tool_test_ok\')"'
    start_out = process_handle_tool.invoke({
        "action": "start",
        "command": cmd,
    })
    assert "Process started in background" in start_out
    assert "Handle ID: proc_" in start_out

    # Extract handle_id
    for line in start_out.splitlines():
        if "Handle ID:" in line:
            hid = line.split("Handle ID:")[1].strip()
            break

    poll_out = _wait_for_terminal_poll(hid)
    assert "TERMINATED with exit code 0" in poll_out

    tail_out = process_handle_tool.invoke({
        "action": "tail",
        "handle_id": hid,
    })
    assert "tool_test_ok" in tail_out
