"""Background Process Handle and Execution Manager (inspired by Prime Agent's rlm/bash.py).

Provides non-blocking background command execution with unblocked process handles,
output tailing, status polling, and asynchronous exit notifications.
"""

from __future__ import annotations

import collections
import logging
import os
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProcessHandle:
    """A live handle to an asynchronous background command."""

    def __init__(
        self,
        handle_id: str,
        command: str,
        process: subprocess.Popen,
        cwd: str,
    ):
        self.handle_id = handle_id
        self.command = command
        self.process = process
        self.cwd = cwd
        self.pid = process.pid
        self.created_at = _now()
        self.completed_at: str | None = None
        self._exit_code: int | None = None
        self._output_lines: collections.deque[str] = collections.deque(maxlen=2000)
        self._reader_thread: threading.Thread | None = None
        self._start_reader()

    def _start_reader(self) -> None:
        def _read_output():
            if self.process.stdout:
                for line in iter(self.process.stdout.readline, ""):
                    if line:
                        self._output_lines.append(line.rstrip())
                self.process.stdout.close()
            self.poll()

        self._reader_thread = threading.Thread(target=_read_output, daemon=True)
        self._reader_thread.start()

    def poll(self) -> int | None:
        """Check if process has terminated; returns exit code or None if running."""
        if self._exit_code is not None:
            return self._exit_code
        ret = self.process.poll()
        if ret is not None:
            self._exit_code = ret
            self.completed_at = _now()
        return ret

    def is_running(self) -> bool:
        return self.poll() is None

    def tail(self, lines: int = 20) -> str:
        """Return the last N lines of output."""
        items = list(self._output_lines)[-lines:]
        return "\n".join(items)

    def output(self) -> str:
        """Return all captured output."""
        return "\n".join(self._output_lines)

    def kill(self) -> bool:
        """Terminate the running process."""
        try:
            self.process.terminate()
            self.process.kill()
            self._exit_code = -9
            self.completed_at = _now()
            return True
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "pid": self.pid,
            "command": self.command,
            "status": "running" if self.is_running() else f"exited({self.poll()})",
            "exit_code": self._exit_code,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class ProcessManager:
    """Registry and controller for background processes."""

    def __init__(self):
        self._processes: dict[str, ProcessHandle] = {}
        self._lock = threading.Lock()

    def start_background(
        self,
        command: str,
        cwd: str | Path | None = None,
    ) -> ProcessHandle:
        """Launch a process in the background without blocking the agent."""
        handle_id = f"proc_{uuid4().hex[:8]}"
        effective_cwd = str(Path(cwd).resolve()) if cwd else os.getcwd()

        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=effective_cwd,
            bufsize=1,
        )

        handle = ProcessHandle(
            handle_id=handle_id,
            command=command,
            process=proc,
            cwd=effective_cwd,
        )

        with self._lock:
            self._processes[handle_id] = handle

        return handle

    def get(self, handle_id: str) -> ProcessHandle | None:
        return self._processes.get(handle_id)

    def list_all(self, running_only: bool = False) -> list[ProcessHandle]:
        with self._lock:
            procs = list(self._processes.values())
        if running_only:
            return [p for p in procs if p.is_running()]
        return procs

    def check_exit_notices(self) -> list[dict[str, Any]]:
        """Collect exit notices for recently completed background jobs."""
        notices: list[dict[str, Any]] = []
        with self._lock:
            for hid, handle in list(self._processes.items()):
                code = handle.poll()
                if code is not None and not getattr(handle, "_notice_emitted", False):
                    handle._notice_emitted = True
                    notices.append({
                        "handle_id": hid,
                        "pid": handle.pid,
                        "command": handle.command,
                        "exit_code": code,
                        "recent_output": handle.tail(10),
                    })
        return notices


_global_process_manager = ProcessManager()


def get_process_manager() -> ProcessManager:
    return _global_process_manager
