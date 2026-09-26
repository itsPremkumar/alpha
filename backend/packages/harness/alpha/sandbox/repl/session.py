"""Stateful Persistent Python REPL Session (inspired by Prime Agent's RLM kernel).

Maintains a persistent namespace across multiple turns, executes code cells
off the event loop with a real wall-clock deadline, captures stdout/stderr,
binds trailing expression results to `_`, and exposes programmatic helpers.

Why the cell body is *not* executed inline
------------------------------------------
Agent-authored cells are arbitrary synchronous Python: ``time.sleep(600)``, an
endless loop, or a ``bash(...)`` call against a hung command. Running those with
``exec``/``eval`` directly on the event loop froze every other coroutine in the
process, and it also made the ``asyncio.wait_for`` deadline unenforceable -- a
timeout callback cannot fire while the loop is blocked inside ``exec``, so the
"30 second" limit only applied to code that happened to yield. The cell body
therefore runs on a dedicated worker thread and the deadline is enforced by the
caller's own clock, which fires regardless of what the worker is doing.
"""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import contextlib
import io
import itertools
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from alpha.sandbox.repl.protocol import CellResult

logger = logging.getLogger(__name__)

_active_sessions: dict[str, ReplSession] = {}

#: Wall-clock ceiling for a single ``bash(...)`` call issued from a cell.
DEFAULT_SHELL_TIMEOUT = 120.0

#: Grace period for reaping a shell that has just been killed. Bounds the kill
#: path itself so a wedged child cannot turn a bounded kill into a hang.
_SHELL_REAP_TIMEOUT = 5.0

#: Bounds the Windows tree-kill helper.
_TREE_KILL_TIMEOUT = 10.0


class ReplTimeoutError(TimeoutError):
    """A REPL cell -- or a shell command it started -- blew its deadline.

    Subclasses the builtin :class:`TimeoutError` so callers that already catch
    ``TimeoutError`` (including the ``python_repl`` tool surface) keep working
    unchanged, while callers that need the deadline itself can read
    :attr:`timeout`.
    """

    def __init__(self, message: str, *, timeout: float) -> None:
        super().__init__(message)
        self.timeout = timeout


# --------------------------------------------------------------------------- #
# Per-thread stdout/stderr capture
# --------------------------------------------------------------------------- #
#
# ``contextlib.redirect_stdout`` swaps the *process-wide* ``sys.stdout``. That was
# harmless while every cell ran inline on the event loop (so exactly one cell
# could be mid-execution), but cells now overlap by design: with a plain global
# redirect, one cell's ``print`` lands in whichever other cell happens to be
# capturing. The proxy below keeps the process stream intact for everyone else
# and routes only the calling thread's writes into that cell's buffer.


class _ThreadRoutedStream:
    """A ``sys.stdout``-shaped stream that captures writes per worker thread."""

    def __init__(self, fallback: Any) -> None:
        self.fallback = fallback
        self._local = threading.local()

    def bind(self, stream: TextIO) -> None:
        self._local.stream = stream

    def unbind(self) -> None:
        self._local.stream = None

    def _target(self) -> Any:
        return getattr(self._local, "stream", None) or self.fallback

    def write(self, data: str) -> int:
        return self._target().write(data)

    def writelines(self, lines: Any) -> None:
        self._target().writelines(lines)

    def flush(self) -> None:
        self._target().flush()

    def isatty(self) -> bool:
        isatty = getattr(self._target(), "isatty", None)
        return bool(isatty()) if callable(isatty) else False

    def fileno(self) -> int:
        return self._target().fileno()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.fallback, name)


class _OutputCapture:
    """Refcounted install/remove of the routing proxies around cell execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth = 0
        self._stdout: _ThreadRoutedStream | None = None
        self._stderr: _ThreadRoutedStream | None = None

    @contextlib.contextmanager
    def capture(self, stdout: io.StringIO, stderr: io.StringIO) -> Iterator[None]:
        with self._lock:
            if self._depth == 0 or sys.stdout is not self._stdout or sys.stderr is not self._stderr:
                # Either the first cell, or the streams were replaced underneath the
                # proxies. The latter is reachable: a cell abandoned by its deadline
                # keeps them installed until its thread unwinds, and anything that
                # swaps ``sys.stdout`` in the meantime (a test harness between
                # phases) would otherwise leave every later cell writing to a proxy
                # that is no longer ``sys.stdout``. Re-wrapping the *underlying*
                # stream keeps proxies from nesting.
                self._stdout = _ThreadRoutedStream(_unwrap_stream(sys.stdout))
                self._stderr = _ThreadRoutedStream(_unwrap_stream(sys.stderr))
                sys.stdout = self._stdout  # type: ignore[assignment]
                sys.stderr = self._stderr  # type: ignore[assignment]
            out_proxy, err_proxy = self._stdout, self._stderr
            self._depth += 1
            out_proxy.bind(stdout)
            err_proxy.bind(stderr)
        try:
            yield
        finally:
            out_proxy.unbind()
            with self._lock:
                self._depth = max(self._depth - 1, 0)
                if self._depth == 0:
                    # Only unwrap our own proxies: anything that replaced the stream
                    # while cells ran must be left alone.
                    if isinstance(sys.stdout, _ThreadRoutedStream):
                        sys.stdout = sys.stdout.fallback
                    if isinstance(sys.stderr, _ThreadRoutedStream):
                        sys.stderr = sys.stderr.fallback
                    self._stdout = None
                    self._stderr = None


def _unwrap_stream(stream: Any) -> Any:
    """Return the real stream behind any of our routing proxies."""
    return stream.fallback if isinstance(stream, _ThreadRoutedStream) else stream


_capture = _OutputCapture()


# --------------------------------------------------------------------------- #
# Bounded shell execution
# --------------------------------------------------------------------------- #


def _start_shell(command: str, *, cwd: str) -> subprocess.Popen[str]:
    """Spawn a shell command in its own process group so it can be killed whole."""
    if os.name == "nt":
        # ``shell=True`` starts ``cmd.exe``; killing it leaves the real command
        # running and still holding the inherited pipe handles, so the group
        # flag plus ``taskkill /T`` is what makes the kill actually reach it.
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(  # noqa: S602 - the REPL shell helper is a shell by design
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            creationflags=creationflags,
        )
    return subprocess.Popen(  # noqa: S602 - the REPL shell helper is a shell by design
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=True,
    )


def _kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate *process* and anything it started, best effort and bounded."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=_TREE_KILL_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            # No ``taskkill`` (stripped image): fall back to the direct child.
            with contextlib.suppress(OSError):
                process.kill()
        return
    with contextlib.suppress(OSError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def _reap_shell(process: subprocess.Popen[str]) -> tuple[str | None, str | None]:
    """Drain a (killed) shell's pipes under its own bounded grace period."""
    try:
        return process.communicate(timeout=_SHELL_REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        # A surviving grandchild is still holding the pipe write end. The output
        # is lost on purpose: an unbounded wait here is the hang being removed.
        return (None, None)


# --------------------------------------------------------------------------- #
# Worker-thread plumbing
# --------------------------------------------------------------------------- #


@dataclass
class _CellRun:
    """Per-cell bookkeeping: the deadline and this cell's shell subprocesses."""

    token: int
    deadline: float

    def remaining(self) -> float:
        return self.deadline - time.monotonic()


@dataclass
class _CellOutcome:
    """A finished cell body, plus a trailing coroutine that needs the loop."""

    result: CellResult
    pending: Awaitable[Any] | None = None


async def _await_worker[T](future: concurrent.futures.Future[T], timeout: float | None) -> T:
    """Await a worker-thread future on the running loop under a real deadline."""

    loop = asyncio.get_running_loop()
    waiter: asyncio.Future[T] = loop.create_future()
    # ``concurrent.futures.Future`` has no ``remove_done_callback``, so delivery
    # is gated on this flag instead: a result that arrives after the caller gave
    # up (deadline, cancellation, closed loop) is dropped rather than delivered
    # to a future nobody awaits.
    state = {"wanted": True}

    def _apply() -> None:
        if waiter.done():
            return
        if future.cancelled():
            waiter.cancel()
            return
        error = future.exception()
        if error is not None:
            waiter.set_exception(error)
        else:
            waiter.set_result(future.result())

    def _on_done(_: concurrent.futures.Future[T]) -> None:
        if not state["wanted"] or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(_apply)
        except RuntimeError:
            # The loop was closed while the worker was still running: there is no
            # caller left to deliver a result to.
            pass

    future.add_done_callback(_on_done)
    try:
        return await asyncio.wait_for(waiter, timeout)
    finally:
        state["wanted"] = False


def _start_worker[T](work: Callable[[], T], *, name: str) -> concurrent.futures.Future[T]:
    """Run *work* on a daemon thread and hand back a future for its result.

    Daemon on purpose: a cell that wedges the interpreter (an endless loop, a
    blocking C call) must not keep the process alive at interpreter exit, which
    is exactly what a ``ThreadPoolExecutor`` worker would do -- CPython joins
    those in an ``atexit`` hook.
    """
    future: concurrent.futures.Future[T] = concurrent.futures.Future()

    def _run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(work())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the loop
            future.set_exception(exc)

    threading.Thread(target=_run, name=name, daemon=True).start()
    return future


class ReplSession:
    """A persistent, stateful Python execution environment.

    Cells are serialised: concurrent ``execute`` calls on one session queue on
    the session lock, and a cell abandoned by its deadline keeps the namespace
    lock until the worker thread actually unwinds. A later cell therefore
    reports a typed timeout rather than interleaving with the runaway one --
    discard the tainted namespace with :meth:`reset` once it has exited.
    """

    def __init__(
        self,
        session_id: str,
        working_dir: Path | str | None = None,
        *,
        shell_timeout: float = DEFAULT_SHELL_TIMEOUT,
    ):
        self.session_id = session_id
        # Resolution is deferred: ``Path.resolve()`` hits the filesystem, and a
        # session is built on the caller's event loop, so doing it here would put
        # a ``getcwd``/``stat`` on the loop. The only consumer is ``bash(...)``,
        # which runs on a worker thread.
        self._working_dir_input = Path(working_dir) if working_dir is not None else None
        self._working_dir: Path | None = None
        #: Deadline applied to a single ``bash(...)`` call from a cell.
        self.shell_timeout = shell_timeout
        self.namespace: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        # Namespace mutations are serialised across worker threads, because a
        # cell abandoned by its deadline can still be unwinding in one of them.
        self._namespace_lock = threading.Lock()
        self._shell_lock = threading.Lock()
        self._shell_registry: dict[int, set[subprocess.Popen[str]]] = {}
        self._cell_ids = itertools.count(1)
        self._cell_local = threading.local()
        self._init_namespace()

    @property
    def working_dir(self) -> Path:
        """The resolved working directory for ``bash(...)`` calls.

        Resolved on first access (a worker thread) and cached from then on.
        """
        resolved = self._working_dir
        if resolved is None:
            resolved = (self._working_dir_input.resolve() if self._working_dir_input is not None else Path.cwd())
            self._working_dir = resolved
        return resolved

    def _init_namespace(self) -> None:
        """Populate initial namespace with standard utilities and helpers."""
        self.namespace = {
            "__name__": "__main__",
            "__doc__": None,
            "__package__": None,
            "Path": Path,
            "os": os,
            "sys": sys,
            "asyncio": asyncio,
            "bash": self._bash_helper,
            "_": None,
        }

    # -- shell subprocess lifecycle ----------------------------------------- #

    def _register_shell(self, token: int | None, process: subprocess.Popen[str]) -> None:
        if token is None:
            return
        with self._shell_lock:
            self._shell_registry.setdefault(token, set()).add(process)

    def _unregister_shell(self, token: int | None, process: subprocess.Popen[str]) -> None:
        if token is None:
            return
        with self._shell_lock:
            processes = self._shell_registry.get(token)
            if processes is None:
                return
            processes.discard(process)
            if not processes:
                self._shell_registry.pop(token, None)

    def _kill_cell_shells(self, token: int) -> int:
        """Kill every shell the cell still owns; returns how many were killed."""
        with self._shell_lock:
            processes = tuple(self._shell_registry.pop(token, ()))
        for process in processes:
            _kill_process_tree(process)
        return len(processes)

    def _abort_cell(self, run: _CellRun) -> None:
        """Abandon a cell: kill the shells it owns, off the loop and without waiting.

        The kill is itself a subprocess (``taskkill``/``killpg``), so doing it
        inline would put a blocking call on the event loop in precisely the
        situation where the loop is already reporting a timeout. It is submitted
        to a worker thread and not awaited: the caller is released at the
        deadline, and the subprocesses still die.
        """
        _start_worker(lambda: self._kill_cell_shells(run.token), name=f"repl-kill-{self.session_id}")

    def live_shell_processes(self) -> tuple[subprocess.Popen[str], ...]:
        """Shell subprocesses started by cells and not yet reaped.

        Exposed so a bounded shell can be *proven* dead rather than merely
        abandoned, and so an operator can see what a session still has running.
        """
        with self._shell_lock:
            return tuple(process for processes in self._shell_registry.values() for process in processes)

    def _bash_helper(self, command: str) -> subprocess.CompletedProcess[str]:
        """Synchronous shell helper available inside the REPL namespace.

        Bounded: the process is killed when the shell deadline expires, and also
        when the cell that started it is itself abandoned, so a hung command can
        no longer pin a worker thread forever.
        """
        process = _start_shell(command, cwd=str(self.working_dir))
        token = getattr(self._cell_local, "token", None)
        self._register_shell(token, process)
        try:
            stdout, stderr = process.communicate(timeout=self.shell_timeout)
        except subprocess.TimeoutExpired:
            self._unregister_shell(token, process)
            _kill_process_tree(process)
            _reap_shell(process)
            raise ReplTimeoutError(
                f"bash() command exceeded its {self.shell_timeout}s deadline and was killed",
                timeout=self.shell_timeout,
            ) from None
        self._unregister_shell(token, process)
        return subprocess.CompletedProcess(command, process.returncode, stdout or "", stderr or "")

    # -- cell execution ------------------------------------------------------ #

    @staticmethod
    def _cell_timeout(timeout: float) -> ReplTimeoutError:
        return ReplTimeoutError(f"REPL cell exceeded its {timeout}s deadline and was interrupted", timeout=timeout)

    async def execute(self, code: str, timeout: float = 30.0) -> CellResult:
        """Execute a code cell asynchronously within the persistent namespace.

        The cell body runs on a worker thread, so a cell that blocks the
        interpreter cannot stall the rest of the process, and *timeout* is a real
        wall-clock deadline: it is enforced from this coroutine, so it fires even
        while the worker is stuck. On expiry the cell's shell subprocesses are
        killed and :class:`ReplTimeoutError` is raised.
        """
        async with self._lock:
            run = _CellRun(token=next(self._cell_ids), deadline=time.monotonic() + timeout)
            future = _start_worker(lambda: self._run_cell_body(code, run), name=f"repl-cell-{self.session_id}")
            try:
                outcome = await _await_worker(future, timeout)
            except ReplTimeoutError:
                # Already typed by the worker (a shell deadline, or a previous
                # cell still holding the namespace).
                raise
            except TimeoutError:
                self._abort_cell(run)
                raise self._cell_timeout(timeout) from None
            except BaseException:
                # Cancellation must not leave the cell's subprocesses running.
                self._abort_cell(run)
                raise

            if outcome.pending is None:
                return outcome.result

            # A trailing coroutine needs a live event loop, which the worker
            # thread does not have. It is awaited here under the cell's original
            # deadline, so the total budget is unchanged.
            remaining = run.remaining()
            if remaining <= 0:
                raise self._cell_timeout(timeout)
            try:
                value = await asyncio.wait_for(outcome.pending, remaining)
            except TimeoutError:
                raise self._cell_timeout(timeout) from None
            bound, result_repr = await self._off_loop(self._bind_trailing_value, value, timeout=max(run.remaining(), 0.001))
            if bound:
                outcome.result.result = value
                outcome.result.result_repr = result_repr
            return outcome.result

    async def _off_loop[T](self, work: Callable[..., T], *args: Any, timeout: float | None = None) -> T:
        """Run a small synchronous callable on a worker thread."""
        return await _await_worker(_start_worker(lambda: work(*args), name=f"repl-cell-{self.session_id}"), timeout)

    def _run_cell_body(self, code: str, run: _CellRun) -> _CellOutcome:
        """Parse/compile/execute a cell body. Runs on a worker thread."""
        # Bounded: a cell abandoned by its deadline can still be unwinding in
        # this session, and a cell that wedged the interpreter holds the lock
        # forever. Either way the caller gets a typed timeout at its own
        # deadline instead of queueing silently behind the runaway cell.
        if not self._namespace_lock.acquire(timeout=max(run.remaining(), 0.0)):
            raise ReplTimeoutError(
                "REPL cell could not start before its deadline: an earlier cell is still running in this session",
                timeout=run.remaining(),
            )
        self._cell_local.token = run.token
        try:
            return self._execute_body(code)
        finally:
            self._cell_local.token = None
            self._namespace_lock.release()

    def _execute_body(self, code: str) -> _CellOutcome:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        try:
            parsed = ast.parse(code)
        except SyntaxError as e:
            return _CellOutcome(
                CellResult(
                    status="error",
                    error_name="SyntaxError",
                    error_value=str(e),
                    traceback=traceback.format_exc(),
                )
            )

        # Check if the trailing node is an expression
        trailing_expr: ast.Expression | None = None
        if parsed.body and isinstance(parsed.body[-1], ast.Expr):
            last_expr_node = parsed.body.pop()
            trailing_expr = ast.Expression(last_expr_node.value)

        # Compile body statements and trailing expression
        body_code = compile(parsed, "<repl>", "exec") if parsed.body else None
        expr_code = compile(trailing_expr, "<repl>", "eval") if trailing_expr else None

        res_val = None
        res_repr = ""

        with _capture.capture(stdout_buf, stderr_buf):
            try:
                # 1. Execute preceding statements
                if body_code:
                    exec(body_code, self.namespace)

                # 2. Evaluate trailing expression if present
                pending: Awaitable[Any] | None = None
                if expr_code:
                    raw_res = eval(expr_code, self.namespace)
                    if asyncio.iscoroutine(raw_res):
                        # Awaiting needs the caller's event loop; hand it back
                        # instead of running a nested loop in this thread.
                        pending = raw_res
                    elif raw_res is not None:
                        res_val = raw_res
                        res_repr = repr(raw_res)
                        self.namespace["_"] = raw_res

                return _CellOutcome(
                    CellResult(
                        status="ok",
                        stdout=stdout_buf.getvalue(),
                        stderr=stderr_buf.getvalue(),
                        result=res_val,
                        result_repr=res_repr,
                    ),
                    pending,
                )

            except Exception as e:
                return _CellOutcome(
                    CellResult(
                        status="error",
                        stdout=stdout_buf.getvalue(),
                        stderr=stderr_buf.getvalue(),
                        error_name=type(e).__name__,
                        error_value=str(e),
                        traceback=traceback.format_exc(),
                    )
                )

    def _bind_trailing_value(self, raw_res: Any) -> tuple[bool, str]:
        """Bind a trailing value that had to be awaited off the worker thread.

        Runs off the event loop: ``repr()`` is user code and may block.
        """
        if raw_res is None:
            return (False, "")
        self.namespace["_"] = raw_res
        return (True, repr(raw_res))

    def reset(self) -> None:
        """Reset the session namespace to initial state."""
        self._init_namespace()


def get_repl_session(
    session_id: str,
    working_dir: Path | str | None = None,
    *,
    shell_timeout: float = DEFAULT_SHELL_TIMEOUT,
) -> ReplSession:
    """Retrieve or construct the persistent ReplSession for a thread/session."""
    if session_id not in _active_sessions:
        _active_sessions[session_id] = ReplSession(session_id, working_dir=working_dir, shell_timeout=shell_timeout)
    return _active_sessions[session_id]
