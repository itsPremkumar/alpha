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

    # Wait for process to complete
    time.sleep(0.6)

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

    time.sleep(0.4)
    poll_out = process_handle_tool.invoke({
        "action": "poll",
        "handle_id": hid,
    })
    assert "TERMINATED with exit code 0" in poll_out

    tail_out = process_handle_tool.invoke({
        "action": "tail",
        "handle_id": hid,
    })
    assert "tool_test_ok" in tail_out
