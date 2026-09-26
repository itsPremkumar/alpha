"""Process execution for the script bridge.

Every ceiling in :class:`~alpha.tools.script_bridge.policy.ScriptBridgeLimits`
is enforced here or in the dispatcher, and every breach is an *error the agent
can read*.  Nothing is silently truncated.

The termination ladder is deliberately two-stage: ``SIGTERM`` first, then
``SIGKILL`` after a grace period, because a script that installs
``signal.SIG_IGN`` for ``SIGTERM`` (or blocks it in C code) must still die.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .env import child_python_executable
from .errors import OutputCapExceeded
from .policy import ScriptBridgeLimits, ScriptBridgeMode

#: Bootstrap executed in the child before the user's script.  It puts the
#: harness packages on ``sys.path`` without inheriting ``PYTHONPATH`` from the
#: host, then hands control to the script.
_CHILD_BOOTSTRAP = r"""
import os, runpy, sys
for _p in {paths!r}:
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["ALPHA_SCRIPT_BRIDGE"] = "1"
runpy.run_path(sys.argv[1], run_name="__script_bridge__")
"""


@dataclass
class CappedStream:
    """A byte-capped sink that records the full text for later paging."""

    name: str
    cap: int
    text: str = ""
    truncated: bool = False
    total_bytes: int = 0
    spill_path: Path | None = None

    def write(self, chunk: str) -> None:
        if not chunk:
            return
        encoded = chunk.encode("utf-8", "replace")
        self.total_bytes += len(encoded)
        self.text += chunk
        if len(self.text.encode("utf-8", "replace")) > self.cap:
            self.truncated = True

    def inline(self, max_inline: int) -> str:
        """Head-and-tail rendering.

        Silent truncation is how an agent concludes something it never saw, so
        the visible text always says how much was dropped, and the full text
        always lives at :attr:`spill_path`.
        """
        if not self.truncated:
            return self.text
        data = self.text.encode("utf-8", "replace")
        if len(data) <= max_inline:
            return self.text
        head = data[: max_inline // 2].decode("utf-8", "replace")
        tail = data[-(max_inline - max_inline // 2) :].decode("utf-8", "replace")
        dropped = len(data) - max_inline
        where = f" full_text={self.spill_path}" if self.spill_path else ""
        return f"[{self.name} head]\n{head}\n[... {dropped} bytes elided; cap={self.cap} total={self.total_bytes} ...]\n[{self.name} tail]\n{tail}\n[{self.name} truncated: showing {max_inline} of {len(data)} bytes.{where}]"


@dataclass
class ExecOutcome:
    """The complete, honest result of one script execution."""

    returncode: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_spill: str | None = None
    stderr_spill: str | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    killed_by: str = ""
    spill_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "stdout_spill": self.stdout_spill,
            "stderr_spill": self.stderr_spill,
            "timed_out": self.timed_out,
            "duration_seconds": round(self.duration_seconds, 4),
            "killed_by": self.killed_by,
            "spill_paths": list(self.spill_paths),
        }


def sys_path_entries() -> list[str]:
    """Directories a child needs so ``import alpha`` resolves.

    Computed from the *parent's* ``sys.path`` and passed explicitly, so no host
    environment variable can widen or narrow what the child can import.
    """
    entries: list[str] = []
    for entry in sys.path:
        if not entry:
            continue
        resolved = str(Path(entry).resolve()) if Path(entry).exists() else entry
        if resolved not in entries:
            entries.append(resolved)
    return entries


def _reader(stream: Any, sink: CappedStream, done: threading.Event) -> None:
    # Read to EOF rather than stopping on ``done``: a chunk that arrives after
    # the kill signal is still output the script produced, and dropping it is
    # exactly the silent-truncation failure this module exists to prevent.
    try:
        for raw in iter(lambda: stream.read(65536), b""):
            sink.write(raw.decode("utf-8", "replace"))
    except (ValueError, OSError):  # pragma: no cover - pipe torn down mid-read
        pass
    finally:
        done.set()
        try:
            stream.close()
        except Exception:  # noqa: BLE001 - best effort
            pass


def terminate_tree(proc: subprocess.Popen[bytes], grace: float) -> str:
    """SIGTERM, then SIGKILL after *grace*.  Returns which stage stopped it.

    A script that ignores ``SIGTERM`` is killed by the second stage; the test
    suite asserts the process is gone either way.
    """
    stage = ""
    try:
        if os.name == "nt":  # pragma: no cover - Windows shape
            # Windows has no SIGTERM to a child; CREATE_NEW_PROCESS_GROUP makes
            # taskkill /T the tree-wide equivalent of killpg.
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=max(grace, 1.0),
            )
            stage = "sigkill"
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
            stage = "sigterm"
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        stage = "terminate_failed"
    try:
        proc.wait(timeout=max(grace, 0.1))
        return stage
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
        else:  # pragma: no cover - Windows shape
            proc.kill()
        proc.wait(timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - defensive
        pass
    return "sigkill"


def run_script(
    script: str,
    *,
    env: dict[str, str],
    mode: ScriptBridgeMode,
    limits: ScriptBridgeLimits,
    cache_dir: Path,
    session_id: str = "oneshot",
) -> ExecOutcome:
    """Execute *script* in a child process under every declared ceiling.

    Raises:
        WallClockTimeout: never - a timeout is reported in
            :attr:`ExecOutcome.timed_out` so the caller can render a readable
            error.  (Kept as a documented exception type for the runner's
            callers that prefer to raise.)
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    stdout_sink = CappedStream("stdout", limits.max_stdout_bytes)
    stderr_sink = CappedStream("stderr", limits.max_stderr_bytes)

    workdir: tempfile.TemporaryDirectory[str] | None = None
    if mode.isolated_interpreter:
        workdir = tempfile.TemporaryDirectory(prefix="alpha-sb-strict-")
        run_cwd = workdir.name
    else:
        run_cwd = mode.cwd or os.getcwd()

    script_path = Path(cache_dir) / f"{session_id}-{int(time.time() * 1000)}.py"
    script_path.write_text(script, encoding="utf-8", newline="\n")

    argv = [child_python_executable()]
    if mode.isolated_interpreter:
        # -I: isolated mode.  No user site directory, no cwd on sys.path, and
        # PYTHON* env vars ignored - which is what makes the import set
        # reproducible.  The bridge still injects the harness paths itself.
        argv.append("-I")
    argv += ["-c", _CHILD_BOOTSTRAP.format(paths=sys_path_entries()), str(script_path)]

    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":  # pragma: no cover - Windows shape
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    started = time.monotonic()
    done = threading.Event()
    proc: subprocess.Popen[bytes] | None = None
    timed_out = False
    killed_by = ""
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False
            argv,
            cwd=run_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs,
        )
        threads = [
            threading.Thread(target=_reader, args=(proc.stdout, stdout_sink, done), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr, stderr_sink, done), daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            proc.wait(timeout=limits.wall_clock_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            done.set()
            killed_by = terminate_tree(proc, limits.sigterm_grace_seconds)
        done.set()
        for thread in threads:
            thread.join(timeout=2.0)
    except OSError as exc:
        stderr_sink.write(f"script bridge could not start a child process: {exc}")
    finally:
        if workdir is not None:
            workdir.cleanup()
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass

    duration = time.monotonic() - started
    stdout_text = stdout_sink.inline(limits.max_output_inline_bytes)
    stderr_text = stderr_sink.inline(limits.max_output_inline_bytes)
    spill_paths: list[str] = []
    stdout_spill = _spill(cache_dir, session_id, "stdout", stdout_sink)
    stderr_spill = _spill(cache_dir, session_id, "stderr", stderr_sink)
    if stdout_spill:
        spill_paths.append(stdout_spill)
    if stderr_spill:
        spill_paths.append(stderr_spill)

    outcome = ExecOutcome(
        returncode=proc.returncode if proc is not None else None,
        stdout=stdout_text,
        stderr=stderr_text,
        stdout_truncated=stdout_sink.truncated,
        stderr_truncated=stderr_sink.truncated,
        stdout_spill=stdout_spill,
        stderr_spill=stderr_spill,
        timed_out=timed_out,
        duration_seconds=duration,
        killed_by=killed_by,
        spill_paths=spill_paths,
    )
    if outcome.stdout_truncated and not outcome.stdout_spill:  # pragma: no cover - defensive
        raise OutputCapExceeded("stdout cap exceeded and the full text could not be cached")
    return outcome


def _spill(cache_dir: Path, session_id: str, stream: str, sink: CappedStream) -> str | None:
    if not sink.truncated:
        return None
    target = cache_dir / f"{session_id}.{stream}.txt"
    try:
        target.write_text(sink.text, encoding="utf-8", newline="\n")
    except OSError:  # pragma: no cover - defensive
        return None
    sink.spill_path = target
    return str(target)
