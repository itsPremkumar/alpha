"""Persistent session kernels for the script bridge.

A kernel is a long-lived child process that keeps its imports, its variables and
its loaded data between cells, so a multi-step workflow costs one turn instead
of N.

THE ENVIRONMENT OF A KERNEL IS FROZEN AT SPAWN.  Changing an environment
variable between cells has no effect, because the child cannot see the parent's
environment after it started.  ``KernelSession.env_fingerprint`` reports what the
kernel was actually started with so an agent can read it instead of guessing.

**A kernel's environment is frozen at spawn; a new environment requires a new
kernel.**  That is the single most common way to lose an hour to this.

Subagent kernels live in their own pool and are **not** counted against the
top-level cap, so a wide fan-out cannot evict a sibling's kernel mid-task.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .env import build_child_env, child_python_executable
from .errors import KernelUnavailable, WallClockTimeout
from .policy import ScriptBridgeLimits
from .runner import sys_path_entries

#: The kernel driver that runs *inside* the child.  Cells arrive on stdin as
#: JSON lines; responses leave on stdout as JSON lines.  The script's own
#: prints are captured into buffers so they cannot corrupt the protocol.
_KERNEL_DRIVER = r"""
import contextlib, io, json, os, sys, traceback

for _p in __BRIDGE_PATHS__:
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

namespace = {"__name__": "__script_bridge_kernel__"}
try:
    import alpha.script_bridge_child as _bridge_client
    namespace["alpha_tools"] = _bridge_client
    namespace["bridge_client"] = _bridge_client
except Exception as _exc:  # pragma: no cover - child-side diagnostics
    namespace["alpha_tools"] = None
    namespace["_bridge_import_error"] = repr(_exc)

_out = sys.stdout
for _stream in (_out,):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        cell = json.loads(line)
    except Exception as exc:
        _out.write(json.dumps({"ok": False, "error": "malformed_cell", "message": str(exc)}) + "\n")
        _out.flush()
        continue
    if cell.get("op") == "bye":
        break
    if cell.get("op") == "reset":
        namespace = {"__name__": "__script_bridge_kernel__"}
        try:
            import alpha.script_bridge_child as _bridge_client
            namespace["alpha_tools"] = _bridge_client
        except Exception:
            pass
        _out.write(json.dumps({"ok": True, "reset": True}) + "\n")
        _out.flush()
        continue
    if cell.get("op") == "probe":
        _out.write(json.dumps({"ok": True, "pid": os.getpid(), "frozen_env": sorted(os.environ)}) + "\n")
        _out.flush()
        continue

    code = cell.get("code", "")
    buf_out, buf_err = io.StringIO(), io.StringIO()
    before = 0
    try:
        from alpha.script_bridge_child.client import get_client as _gc
        before = _gc().calls_made if _gc is not None else 0
    except Exception:
        before = 0
    error = None
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            exec(compile(code, "<script_bridge_cell>", "exec"), namespace, namespace)
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()[-4000:]}
    after = 0
    try:
        from alpha.script_bridge_child.client import get_client as _gc
        after = _gc().calls_made if _gc is not None else 0
    except Exception:
        after = 0
    _out.write(json.dumps({
        "ok": error is None,
        "stdout": buf_out.getvalue(),
        "stderr": buf_err.getvalue(),
        "error": error,
        "tool_calls": max(0, after - before),
    }, ensure_ascii=False) + "\n")
    _out.flush()
"""


def env_fingerprint(env: dict[str, str]) -> str:
    """A stable fingerprint of a child environment, for honest reporting."""
    payload = json.dumps(dict(sorted(env.items())), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class CellResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    error: dict[str, Any] | None = None
    tool_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "tool_calls": self.tool_calls,
        }


@dataclass
class KernelSession:
    """One live kernel process plus the dispatcher that serves it."""

    session_id: str
    owner: str
    proc: subprocess.Popen[bytes]
    env: dict[str, str]
    cwd: str
    created_at: float
    last_used: float
    server: Any = None
    dispatcher: Any = None
    spawn_degraded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def env_fingerprint(self) -> str:
        return env_fingerprint(self.env)

    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    def alive(self) -> bool:
        return self.proc.poll() is None

    def describe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "owner": self.owner,
            "pid": self.proc.pid,
            "cwd": self.cwd,
            "age_seconds": round(self.age_seconds(), 3),
            "idle_seconds": round(self.idle_seconds(), 3),
            "env_fingerprint": self.env_fingerprint,
            "env_frozen_at_spawn": True,
            "alive": self.alive(),
            "spawn_degraded": self.spawn_degraded,
            "notes": list(self.notes),
        }


class KernelManager:
    """Owns every kernel for one bridge instance.

    Two pools: ``top`` (this session) and ``subagent:<id>``.  Each pool has its
    own cap, so a subagent fan-out never evicts the top-level kernel and a
    top-level eviction never reaches into a subagent's pool.
    """

    def __init__(self, limits: ScriptBridgeLimits, cache_dir: Path) -> None:
        self.limits = limits
        self.cache_dir = cache_dir
        self._sessions: dict[str, KernelSession] = {}
        self._lock = threading.RLock()
        self.degradations: list[str] = []

    # -- pools -------------------------------------------------------------
    def _pool(self, owner: str) -> list[KernelSession]:
        return [s for s in self._sessions.values() if s.owner == owner]

    def _cap_for(self, owner: str) -> int:
        if owner == "top":
            return max(1, self.limits.max_kernel_sessions)
        # Subagent kernels get their own, larger budget.  They never consume the
        # top-level cap, and a top-level eviction never reaches into a subagent
        # pool - otherwise a wide fan-out would evict a sibling mid-task.
        return max(1, self.limits.subagent_max_kernel_sessions)

    # -- lifecycle ---------------------------------------------------------
    def spawn(
        self,
        session_id: str,
        *,
        owner: str = "top",
        env_extra: dict[str, str] | None = None,
        env_opt_in: dict[str, str] | None = None,
        cwd: str | None = None,
        dispatcher_factory: Any = None,
    ) -> KernelSession:
        """Start a kernel, or raise :class:`KernelUnavailable`.

        The environment is built here, once.  It is never rebuilt for a later
        cell, which is precisely why it is reported as frozen.
        """
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.alive():
                    return existing
                self._sessions.pop(session_id, None)

            self._evict_locked(owner, excluding=session_id)

            env = build_child_env(opt_in=env_opt_in, extra=env_extra)
            driver = _KERNEL_DRIVER.replace("__BRIDGE_PATHS__", repr(sys_path_entries()))
            driver_path = self.cache_dir / f"kernel-driver-{session_id}.py"
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            driver_path.write_text(driver, encoding="utf-8", newline="\n")
            argv = [child_python_executable(), "-I", str(driver_path)]
            popen_kwargs: dict[str, Any] = {}
            if os.name == "nt":  # pragma: no cover - Windows shape
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            run_cwd = cwd or os.getcwd()
            try:
                proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False
                    argv,
                    cwd=run_cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **popen_kwargs,
                )
            except (OSError, ValueError) as exc:
                raise KernelUnavailable(
                    "a persistent kernel could not be spawned on this backend; fall back to one-shot script execution",
                    session_id=session_id,
                    cause=f"{type(exc).__name__}: {exc}",
                ) from exc
            now = time.monotonic()
            session = KernelSession(
                session_id=session_id,
                owner=owner,
                proc=proc,
                env=env,
                cwd=run_cwd,
                created_at=now,
                last_used=now,
            )
            self._sessions[session_id] = session
            if dispatcher_factory is not None:
                session.dispatcher = dispatcher_factory(session)
            return session

    def _evict_locked(self, owner: str, *, excluding: str | None = None) -> list[str]:
        """Evict oldest-first within *owner*'s pool only."""
        evicted: list[str] = []
        pool = [s for s in self._pool(owner) if s.session_id != excluding]
        cap = self._cap_for(owner)
        while len(pool) >= cap:
            oldest = min(pool, key=lambda s: s.last_used)
            self._terminate(oldest)
            self._sessions.pop(oldest.session_id, None)
            evicted.append(oldest.session_id)
            pool.remove(oldest)
        return evicted

    def _terminate(self, session: KernelSession) -> None:
        if session.alive():
            try:
                session.proc.kill()
            except OSError:  # pragma: no cover - defensive
                pass
            try:
                session.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass

    def reap_idle(self) -> list[str]:
        """Drop kernels idle past the idle timeout.  Returns the ids dropped."""
        dropped: list[str] = []
        with self._lock:
            for sid, session in list(self._sessions.items()):
                if session.idle_seconds() > self.limits.kernel_idle_seconds:
                    self._terminate(session)
                    self._sessions.pop(sid, None)
                    dropped.append(sid)
        return dropped

    def reset(self, session_id: str | None = None) -> list[str]:
        """Explicit reset.  Drops one kernel, or every kernel in the top pool."""
        with self._lock:
            targets = [s for s in self._sessions.values() if s.session_id == session_id] if session_id else [s for s in self._sessions.values() if s.owner == "top"]
            for session in targets:
                self._terminate(session)
                self._sessions.pop(session.session_id, None)
            return [s.session_id for s in targets]

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.describe() for s in self._sessions.values()]

    def get(self, session_id: str) -> KernelSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    # -- cell execution ----------------------------------------------------
    def run_cell(
        self,
        session: KernelSession,
        code: str,
        *,
        timeout: float | None = None,
    ) -> CellResult:
        """Send one cell to a live kernel and read exactly one response."""
        if not session.alive():
            raise KernelUnavailable(
                "kernel process is not running; reset the kernel or fall back to one-shot",
                session_id=session.session_id,
            )
        budget = self.limits.wall_clock_seconds if timeout is None else timeout
        payload = json.dumps({"op": "exec", "code": code}, ensure_ascii=False) + "\n"
        try:
            assert session.proc.stdin is not None
            session.proc.stdin.write(payload.encode("utf-8"))
            session.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise KernelUnavailable(
                "kernel stdin is closed; the process died between cells",
                session_id=session.session_id,
                cause=f"{type(exc).__name__}: {exc}",
            ) from exc
        line = self._read_line(session, budget)
        session.last_used = time.monotonic()
        if line is None:
            raise WallClockTimeout(
                "kernel cell exceeded its wall-clock budget and the kernel was killed",
                session_id=session.session_id,
                wall_clock_seconds=budget,
            )
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KernelUnavailable(
                "kernel returned a malformed response frame",
                session_id=session.session_id,
                cause=str(exc),
            ) from exc
        return CellResult(
            ok=bool(data.get("ok")),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            error=data.get("error"),
            tool_calls=int(data.get("tool_calls", 0) or 0),
        )

    def _read_line(self, session: KernelSession, budget: float) -> str | None:
        """Read one response line, enforcing the wall clock from this thread."""
        result: dict[str, Any] = {}

        def _reader() -> None:
            try:
                assert session.proc.stdout is not None
                result["line"] = session.proc.stdout.readline()
            except (ValueError, OSError) as exc:  # pragma: no cover - defensive
                result["error"] = exc

        thread = threading.Thread(target=_reader, daemon=True, name=f"sb-kernel-{session.session_id}")
        thread.start()
        thread.join(timeout=budget)
        if thread.is_alive():
            self._terminate(session)
            return None
        if "error" in result:  # pragma: no cover - defensive
            raise KernelUnavailable("kernel stdout closed", session_id=session.session_id, cause=repr(result["error"]))
        line = result.get("line") or ""
        return line or None

    def shutdown(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                self._terminate(session)
            self._sessions.clear()
