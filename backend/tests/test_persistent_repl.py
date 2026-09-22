"""Unit tests for RLM Persistent Python REPL Kernel."""

import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.sandbox.repl.session import ReplSession
from alpha.tools.builtins.python_repl_tool import python_repl_tool


def _tool_runtime() -> ToolRuntime:
    """A minimal ``ToolRuntime`` for direct ``ainvoke`` calls.

    ``runtime`` is an injected argument: in the graph, ToolNode fills it in
    before the tool is called. Calling the tool directly does not go through
    ToolNode, so the caller has to supply it — ``args_schema`` marks it
    required, and omitting it fails validation.
    """
    return ToolRuntime(
        state={},
        context={"thread_id": "tool_test_thread"},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="tool-call-1",
        store=None,
    )


@pytest.mark.asyncio
async def test_repl_variable_persistence_across_cells():
    session = ReplSession("test_session_1")

    # Cell 1: Define variables and functions
    res1 = await session.execute("""
x = 100
def square(n):
    return n * n
""")
    assert res1.status == "ok"
    assert session.namespace["x"] == 100

    # Cell 2: Use variables defined in Cell 1
    res2 = await session.execute("square(x)")
    assert res2.status == "ok"
    assert res2.result == 10000
    assert res2.result_repr == "10000"
    assert session.namespace["_"] == 10000

    # Cell 3: Further derivation
    res3 = await session.execute("_ + 1")
    assert res3.status == "ok"
    assert res3.result == 10001
    assert session.namespace["_"] == 10001


@pytest.mark.asyncio
async def test_repl_stdout_stderr_capture():
    session = ReplSession("test_session_2")

    res = await session.execute("""
import sys
print("Standard output test")
print("Standard error test", file=sys.stderr)
42
""")
    assert res.status == "ok"
    assert "Standard output test" in res.stdout
    assert "Standard error test" in res.stderr
    assert res.result == 42
    output = res.format_output()
    assert "[stdout]" in output
    assert "[stderr]" in output
    assert "[result]\n42" in output


@pytest.mark.asyncio
async def test_repl_error_isolation():
    session = ReplSession("test_session_3")

    # Set variable
    await session.execute("valid_var = 'preserved'")

    # Cell with error
    res_err = await session.execute("1 / 0")
    assert res_err.status == "error"
    assert res_err.error_name == "ZeroDivisionError"

    # Subsequent cell still has valid_var intact
    res_sub = await session.execute("valid_var")
    assert res_sub.status == "ok"
    assert res_sub.result == "preserved"


@pytest.mark.asyncio
async def test_repl_preloaded_utilities():
    session = ReplSession("test_session_4")

    res = await session.execute("""
p = Path(".").resolve()
isinstance(p, Path)
""")
    assert res.status == "ok"
    assert res.result is True


@pytest.mark.asyncio
async def test_python_repl_tool():
    runtime = _tool_runtime()

    out1 = await python_repl_tool.ainvoke({
        "code": "a = 50\na * 3",
        "session_id": "tool_test_session",
        "runtime": runtime,
    })
    assert "150" in out1

    out2 = await python_repl_tool.ainvoke({
        "code": "a + 20",
        "session_id": "tool_test_session",
        "runtime": runtime,
    })
    assert "70" in out2
