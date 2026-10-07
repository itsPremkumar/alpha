"""Built-in Code-Mode tool for programmatic tool execution in a single model turn."""

# NOTE: no ``from __future__ import annotations`` — under PEP 563 the ``runtime: Runtime``
# annotation below becomes the *string* "Runtime", and LangChain's injected-argument
# detection inspects the annotation object, so a string never matches. ``runtime`` would then
# never be registered as injected (see ``alpha.tools.types.Runtime``).

from langchain.tools import tool

from alpha.tools.code_mode.bridge import execute_code_mode
from alpha.tools.code_mode.tool import get_default_bridge
from alpha.tools.discovery.code_mode import CodeModeGate, CodeModeUnavailable
from alpha.tools.discovery.config import DiscoveryConfig
from alpha.tools.types import Runtime


@tool("code_mode", parse_docstring=True)
def code_mode_tool(
    runtime: Runtime,
    code: str,
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
    # FAIL-CLOSED: the isolated code bridge is NOT built on this host. The only
    # way a model ever reaches in-process `exec()` with a live ToolBridge is
    # for the caller to explicitly request CODE mode AND provide a
    # permission-modeled runtime (Node/Deno/Bun with a filesystem/network grant
    # system). Anything else gets CodeModeUnavailable and never reaches
    # execute_code_mode, so an ungated model cannot turn this tool into an
    # arbitrary-code primitive inside the Gateway process.
    try:
        CodeModeGate(DiscoveryConfig()).require_code_mode()
    except CodeModeUnavailable as exc:
        return f"CodeModeUnavailable: {exc}"
    bridge = get_default_bridge()
    res = execute_code_mode(code, bridge=bridge)
    return res.format_output()
