"""In-memory programmatic ToolBridge and execution engine for Code-Mode."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    success: bool = True


@dataclass
class CodeExecutionResult:
    stdout: str
    stderr: str
    result: Any
    tool_calls: list[ToolCallRecord]
    success: bool
    error: str | None = None

    def format_output(self) -> str:
        lines = []
        if self.tool_calls:
            lines.append(f"==> Programmatic Tool Invocations ({len(self.tool_calls)}):")
            for idx, call in enumerate(self.tool_calls, 1):
                status = "OK" if call.success else f"FAIL ({call.error})"
                lines.append(f"  [{idx}] {call.tool_name}({call.arguments}) -> {status} ({call.duration_ms:.1f}ms)")
        if self.stdout.strip():
            lines.append("==> Stdout:")
            lines.append(self.stdout.strip())
        if self.stderr.strip():
            lines.append("==> Stderr:")
            lines.append(self.stderr.strip())
        if self.error:
            lines.append(f"==> Execution Error: {self.error}")
        elif self.result is not None:
            lines.append(f"==> Final Result: {self.result}")
        return "\n".join(lines)


class ToolBridge:
    """Provides in-memory programmatic invocation of tools within code execution blocks."""

    def __init__(self, registry: dict[str, Callable] | None = None):
        self._tools: dict[str, Callable] = dict(registry) if registry else {}
        self.call_history: list[ToolCallRecord] = []

    def register(self, name: str, fn: Callable) -> None:
        self._tools[name] = fn

    def register_many(self, tools: dict[str, Callable]) -> None:
        self._tools.update(tools)

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def call(self, tool_name: str, **kwargs: Any) -> Any:
        """Call a registered tool by name with arbitrary keyword arguments."""
        if tool_name not in self._tools:
            # Fallback check with lowercase
            matches = [k for k in self._tools if k.lower() == tool_name.lower()]
            if matches:
                tool_name = matches[0]
            else:
                err = f"Tool '{tool_name}' is not registered in ToolBridge. Available tools: {self.list_tools()}"
                self.call_history.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        arguments=kwargs,
                        error=err,
                        success=False,
                    )
                )
                raise KeyError(err)

        fn = self._tools[tool_name]
        start_t = time.perf_counter()
        try:
            # Handle async vs sync tool callables
            if inspect.iscoroutinefunction(fn):
                try:
                    loop = asyncio.get_running_loop()
                    # In an active loop, schedule or create task
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        res = pool.submit(asyncio.run, fn(**kwargs)).result()
                except RuntimeError:
                    res = asyncio.run(fn(**kwargs))
            else:
                # Handle BaseTool from langchain if invoke/run is preferred
                if hasattr(fn, "invoke") and callable(fn.invoke):
                    res = fn.invoke(kwargs)
                else:
                    res = fn(**kwargs)

            duration = (time.perf_counter() - start_t) * 1000.0
            self.call_history.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=kwargs,
                    result=res,
                    duration_ms=duration,
                    success=True,
                )
            )
            return res
        except Exception as e:
            duration = (time.perf_counter() - start_t) * 1000.0
            err_msg = str(e) or type(e).__name__
            self.call_history.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=kwargs,
                    error=err_msg,
                    duration_ms=duration,
                    success=False,
                )
            )
            raise

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Allows calling tools as methods: tools.read_file(path='...')"""
        if name in self._tools or any(k.lower() == name.lower() for k in self._tools):
            return lambda **kwargs: self.call(name, **kwargs)
        raise AttributeError(f"ToolBridge has no tool named '{name}'")


def execute_code_mode(
    code: str,
    bridge: ToolBridge | None = None,
    globals_dict: dict[str, Any] | None = None,
) -> CodeExecutionResult:
    """Execute a Python snippet with the in-memory tool bridge bound as `tools`."""
    bridge = bridge or ToolBridge()
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    exec_globals: dict[str, Any] = {
        "__builtins__": __builtins__,
        "tools": bridge,
        "json": __import__("json"),
        "re": __import__("re"),
        "math": __import__("math"),
        "time": __import__("time"),
    }
    if globals_dict:
        exec_globals.update(globals_dict)

    success = True
    error_str = None
    result_val = None

    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        try:
            # First try parsing as single expression
            parsed = compile(code, "<code_mode>", "exec")
            exec(parsed, exec_globals)
            # Capture last variable or explicit `result` variable if present
            if "result" in exec_globals and exec_globals["result"] is not None:
                result_val = exec_globals["result"]
        except Exception:
            success = False
            error_str = traceback.format_exc()

    return CodeExecutionResult(
        stdout=stdout_buf.getvalue(),
        stderr=stderr_buf.getvalue(),
        result=result_val,
        tool_calls=list(bridge.call_history),
        success=success,
        error=error_str,
    )
