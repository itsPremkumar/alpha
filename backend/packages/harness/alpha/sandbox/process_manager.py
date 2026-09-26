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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class ProcessSpawnError(RuntimeError):
    """Raised when a background command could not be spawned at all."""


#: Seconds ``ProcessHandle.await_outcome`` waits for an immediately-observable
#: failure before the start path reports a command as running. Kept short so
#: launching a long-lived server costs one short settle, not a stall.
START_SETTLE_SECONDS = 0.75

#: Seconds ``ProcessHandle.kill`` waits to confirm the process really exited
#: before refusing to report the kill as successful.
_KILL_CONFIRM_TIMEOUT_SECONDS = 5.0

#: Status recorded for a process that is confirmed dead but whose own status the
#: OS never reported. Deliberately a non-zero failure sentinel: it is NOT the
#: process's exit code, and it must never read as a success. Replaces the old
#: hard-coded ``-9``, which looked like a real wait-status to every consumer.
UNREPORTED_EXIT_STATUS = -1


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

    def await_outcome(self, timeout: float) -> int | None:
        """Wait up to *timeout* seconds for a terminal state.

        Returns the real exit code if the process terminated within the window,
        or ``None`` if it is still running when the window closes. Used by the
        start path so an immediately-failing command is reported as the failure
        it is instead of as a successful spawn.
        """
        if timeout <= 0:
            return self.poll()
        deadline = time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None or time.monotonic() >= deadline:
                return code
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def tail(self, lines: int = 20) -> str:
        """Return the last N lines of output."""
        items = list(self._output_lines)[-lines:]
        return "\n".join(items)

    def output(self) -> str:
        """Return all captured output."""
        return "\n".join(self._output_lines)

    def kill(self) -> bool:
        """Terminate the running process and verify it actually went away.

        Returns True only when the process is observed dead. A previous
        implementation returned True whenever ``terminate()``/``kill()`` did
        not raise, and stamped a fabricated exit code of ``-9`` onto the
        handle. That reported a success it had not verified and then handed the
        model an exit status the process never produced -- which
        ``process_handle(action='poll')`` and ``list`` both published as if it
        were real.
        """
        try:
            self.process.terminate()
            self.process.kill()
        except (OSError, ValueError) as exc:
            # A process that already exited is not a kill failure; anything
            # else is, and the caller must be told the truth about it.
            if self.process.poll() is None:
                logger.warning("Failed to terminate process %s: %s", self.pid, exc)
                return False
        return self.await_death(timeout=_KILL_CONFIRM_TIMEOUT_SECONDS)

    def await_death(self, timeout: float) -> bool:
        """Reap the process, returning True only when it is really gone."""
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("Process %s did not exit after kill()", self.pid)
            return False
        # Record the status the OS actually reported. Never invent one.
        self.poll()
        if self._exit_code is None:
            # Reaped, but the OS never reported a status. The process IS dead,
            # so the handle must not keep claiming to be running, but it also
            # must not be handed a success. Use the explicit non-zero sentinel.
            self._exit_code = UNREPORTED_EXIT_STATUS
            self.completed_at = _now()
        return True

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
        """Launch a process in the background without blocking the agent.

        Raises:
            ProcessSpawnError: If the process could not be spawned. Callers must
                report this as a failure; there is no handle to poll, so a
                spawn failure can never be dressed up as a started process.
        """
        handle_id = f"proc_{uuid4().hex[:8]}"
        effective_cwd = str(Path(cwd).resolve()) if cwd else os.getcwd()

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=effective_cwd,
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise ProcessSpawnError(f"could not spawn background process: {type(exc).__name__}: {exc}") from exc

        if proc.pid is None or proc.pid <= 0:
            # Popen returned without a real process: report the spawn as
            # failed rather than handing out a handle that can never run.
            raise ProcessSpawnError("spawn returned no usable process id")

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
