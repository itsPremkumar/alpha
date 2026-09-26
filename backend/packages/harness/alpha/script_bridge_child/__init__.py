"""Child-side runtime for the script bridge.

This package is what a sandboxed script imports.  It is deliberately a
**standalone** package: importing it must not drag in the 130-tool registry, the
LangChain stack or any provider SDK, because a cold ``import alpha.tools`` on
this host costs minutes and would make the bridge slower than the tool calls it
saves.  ``import alpha`` is cheap; everything below this line is cheap too.

Nothing here is trusted.  The functions raise on anything they are not told
they may do, and the parent re-derives the allowlist, the limits and the
authorisation decision for every single call regardless.
"""

from __future__ import annotations

from .client import ToolRpcClient, get_client, reset_client
from .generated import STUB_REGISTRY_SHA256, available_tool_names, tool

__all__ = [
    "STUB_REGISTRY_SHA256",
    "ToolRpcClient",
    "available_tool_names",
    "get_client",
    "reset_client",
    "tool",
]
