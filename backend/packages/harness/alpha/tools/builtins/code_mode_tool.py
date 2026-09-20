"""Built-in Code-Mode tool for programmatic tool execution in a single model turn."""

from __future__ import annotations

from langchain.tools import tool

from alpha.tools.code_mode.bridge import execute_code_mode
from alpha.tools.code_mode.tool import get_default_bridge
from alpha.tools.types import Runtime


@tool("code_mode", parse_docstring=True)
def code_mode_tool(
    code: str,
    runtime: Runtime | None = None,
) -> str:
    """Execute Python code that programmatically invokes other tools via `tools.call(name, **kwargs)` or `tools.<name>(**kwargs)`.

    This eliminates serial model turns by chaining multiple tool calls, data filtering,
    regex transformations, and math calculations inside one single execution environment.

    Example:
    ```python
    content = tools.call("echo", text="hello world")
    print(f"Processed: {content.upper()}")
    result = {"status": "ok", "length": len(content)}
    ```

    Args:
        code: Python script orchestrating calls through the `tools` object.
    """
    bridge = get_default_bridge()
    res = execute_code_mode(code, bridge=bridge)
    return res.format_output()
