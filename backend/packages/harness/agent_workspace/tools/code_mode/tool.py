"""High-level code_mode tool evaluator."""

from __future__ import annotations

from agent_workspace.tools.code_mode.bridge import ToolBridge, execute_code_mode

_global_bridge = ToolBridge()


def register_default_tools(bridge: ToolBridge) -> None:
    """Register standard helper tools into a bridge if not already present."""
    if "echo" not in bridge._tools:
        bridge.register("echo", lambda text="": text)
    if "read_text_file" not in bridge._tools:
        def _read_file(path: str) -> str:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        bridge.register("read_text_file", _read_file)


def get_default_bridge() -> ToolBridge:
    register_default_tools(_global_bridge)
    return _global_bridge


def code_mode_eval(code: str, bridge: ToolBridge | None = None) -> str:
    """Execute a Python snippet against the programmatic tool bridge and format response."""
    b = bridge or get_default_bridge()
    res = execute_code_mode(code, bridge=b)
    return res.format_output()
