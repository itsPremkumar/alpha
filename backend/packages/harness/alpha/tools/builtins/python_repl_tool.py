"""Built-in RLM Python REPL tool (inspired by Prime Agent's programmatic REPL)."""

from __future__ import annotations

from langchain.tools import tool

from alpha.sandbox.repl.session import get_repl_session
from alpha.tools.types import Runtime


@tool("python_repl", parse_docstring=True)
async def python_repl_tool(
    code: str,
    runtime: Runtime,
    timeout: float = 30.0,
    session_id: str | None = None,
) -> str:
    """Execute Python code in a persistent, stateful REPL kernel.

    Treats context as variables (prompt-as-a-variable) and tools/subagents as code.
    Variables, imports, functions, and state are preserved across turns within the
    same session/thread.

    Standard utilities (Path, os, sys, asyncio, bash) are preloaded.
    The result of trailing expressions is bound to `_` and returned.

    Args:
        code: Python code block to execute in the persistent session.
        timeout: Maximum execution timeout in seconds. Defaults to 30.0.
        session_id: Optional session identifier. When omitted, automatically binds to current thread_id.
    """
    effective_session_id = session_id
    if not effective_session_id and runtime and hasattr(runtime, "context") and isinstance(runtime.context, dict):
        effective_session_id = runtime.context.get("thread_id")

    if not effective_session_id:
        effective_session_id = "default_session"

    session = get_repl_session(effective_session_id)
    try:
        cell_result = await session.execute(code, timeout=timeout)
        return cell_result.format_output()
    except TimeoutError:
        return f"Error: Python execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing Python code: {e}"
