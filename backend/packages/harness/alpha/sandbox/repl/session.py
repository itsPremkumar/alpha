"""Stateful Persistent Python REPL Session (inspired by Prime Agent's RLM kernel).

Maintains a persistent namespace across multiple turns, executes code cells
asynchronously, captures stdout/stderr, binds trailing expression results to `_`,
and exposes programmatic helpers.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from alpha.sandbox.repl.protocol import CellResult

logger = logging.getLogger(__name__)

_active_sessions: dict[str, ReplSession] = {}


class ReplSession:
    """A persistent, stateful Python execution environment."""

    def __init__(self, session_id: str, working_dir: Path | str | None = None):
        self.session_id = session_id
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.namespace: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._init_namespace()

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

    def _bash_helper(self, command: str) -> subprocess.CompletedProcess[str]:
        """Synchronous/asynchronous shell helper available inside the REPL namespace."""
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(self.working_dir),
        )
        return result

    async def execute(self, code: str, timeout: float = 30.0) -> CellResult:
        """Execute a code cell asynchronously within the persistent namespace."""
        async with self._lock:
            return await asyncio.wait_for(self._run_code(code), timeout=timeout)

    async def _run_code(self, code: str) -> CellResult:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        try:
            parsed = ast.parse(code)
        except SyntaxError as e:
            return CellResult(
                status="error",
                error_name="SyntaxError",
                error_value=str(e),
                traceback=traceback.format_exc(),
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

        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            try:
                # 1. Execute preceding statements
                if body_code:
                    exec(body_code, self.namespace)

                # 2. Evaluate trailing expression if present
                if expr_code:
                    raw_res = eval(expr_code, self.namespace)
                    if asyncio.iscoroutine(raw_res):
                        raw_res = await raw_res

                    if raw_res is not None:
                        res_val = raw_res
                        res_repr = repr(raw_res)
                        self.namespace["_"] = raw_res

                return CellResult(
                    status="ok",
                    stdout=stdout_buf.getvalue(),
                    stderr=stderr_buf.getvalue(),
                    result=res_val,
                    result_repr=res_repr,
                )

            except Exception as e:
                return CellResult(
                    status="error",
                    stdout=stdout_buf.getvalue(),
                    stderr=stderr_buf.getvalue(),
                    error_name=type(e).__name__,
                    error_value=str(e),
                    traceback=traceback.format_exc(),
                )

    def reset(self) -> None:
        """Reset the session namespace to initial state."""
        self._init_namespace()


def get_repl_session(session_id: str, working_dir: Path | str | None = None) -> ReplSession:
    """Retrieve or construct the persistent ReplSession for a thread/session."""
    if session_id not in _active_sessions:
        _active_sessions[session_id] = ReplSession(session_id, working_dir=working_dir)
    return _active_sessions[session_id]
