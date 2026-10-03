"""Built-in RLM Python REPL tool (inspired by Prime Agent's programmatic REPL)."""

# NOTE: deliberately NO ``from __future__ import annotations`` here.
# Under PEP 563 the ``runtime: Runtime`` annotation below is the *string*
# "Runtime", and LangChain's injected-argument detection
# (``_is_injected_arg_type``) inspects the annotation object — a string never
# matches, so ``runtime`` is never registered as injected. It then lands in
# ``args_schema`` as a required field, and any call that does not go through
# ToolNode (which injects by the parameter *name*) fails validation with
# "runtime: Field required". Keeping annotations as real objects fixes that.
import asyncio

from langchain.tools import tool

from alpha.config import get_app_config
from alpha.sandbox.repl.session import ReplTimeoutError, get_repl_session
from alpha.sandbox.security import (
    LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE,
    is_in_process_repl_allowed,
)
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

    The cell runs on a worker thread, so blocking code (sleeps, an endless
    loop, a hung ``bash``) does not stall the rest of the agent. Exceeding
    ``timeout`` interrupts the cell: the shell subprocesses it started are
    killed and the cell reports a timeout instead of hanging.

    This tool runs ``exec()`` inside the Gateway process itself. It has no
    sandbox boundary and is therefore disabled unless
    ``sandbox.allow_in_process_repl`` is explicitly enabled.

    Args:
        code: Python code block to execute in the persistent session.
        timeout: Maximum execution timeout in seconds. Defaults to 30.0.
        session_id: Optional session identifier. When omitted, automatically binds to current thread_id.
    """
    # Defence in depth. Assembly-time filtering (``_is_host_bash_tool`` and the
    # built-in filter in ``tools.py``) already keeps this tool out of the model
    # schema when the switch is off, so the model normally never sees it. This
    # check is the second half: a tool that reached a runtime some other way —
    # a direct call, a custom Agent tool list, a config path that bypassed
    # assembly — still refuses rather than handing out process-level execution.
    #
    # ``get_app_config()`` stats the config file on every call to detect edits,
    # which is blocking; this tool body runs on the event loop, so resolve it in
    # a worker thread (same treatment as ``alpha.runtime.context_compaction``).
    app_config = await asyncio.to_thread(get_app_config)
    if not is_in_process_repl_allowed(app_config):
        return LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE

    effective_session_id = session_id
    if not effective_session_id and runtime and hasattr(runtime, "context") and isinstance(runtime.context, dict):
        effective_session_id = runtime.context.get("thread_id")

    if not effective_session_id:
        effective_session_id = "default_session"

    session = get_repl_session(effective_session_id)
    try:
        cell_result = await session.execute(code, timeout=timeout)
        return cell_result.format_output()
    except ReplTimeoutError as exc:
        return f"Error: Python execution timed out after {timeout} seconds ({exc})."
    except TimeoutError:
        return f"Error: Python execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing Python code: {e}"
