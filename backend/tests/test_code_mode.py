import pytest

from agent_workspace.tools.builtins.code_mode_tool import code_mode_tool
from agent_workspace.tools.code_mode.bridge import ToolBridge, execute_code_mode


def test_tool_bridge_register_and_call():
    bridge = ToolBridge()
    bridge.register("add", lambda a, b: a + b)
    bridge.register("greet", lambda name="friend": f"Hello, {name}!")

    res1 = bridge.call("add", a=10, b=25)
    assert res1 == 35

    res2 = bridge.greet(name="Alice")
    assert res2 == "Hello, Alice!"

    assert len(bridge.call_history) == 2
    assert bridge.call_history[0].tool_name == "add"
    assert bridge.call_history[0].success is True
    assert bridge.call_history[1].tool_name == "greet"


def test_tool_bridge_missing_tool():
    bridge = ToolBridge()
    with pytest.raises(KeyError, match="not registered"):
        bridge.call("unknown_tool", x=1)

    assert len(bridge.call_history) == 1
    assert bridge.call_history[0].success is False


def test_execute_code_mode_multi_tool_chain():
    bridge = ToolBridge()
    bridge.register("fetch_user", lambda uid: {"id": uid, "name": "Agent 007", "role": "admin"})
    bridge.register("format_badge", lambda name, role: f"[{role.upper()}] {name}")

    script = """
user = tools.fetch_user(uid=42)
badge = tools.format_badge(name=user["name"], role=user["role"])
print(f"Generated badge: {badge}")
result = {"badge": badge, "valid": True}
"""
    exec_res = execute_code_mode(script, bridge=bridge)
    assert exec_res.success is True
    assert exec_res.result == {"badge": "[ADMIN] Agent 007", "valid": True}
    assert "Generated badge: [ADMIN] Agent 007" in exec_res.stdout
    assert len(exec_res.tool_calls) == 2


def test_code_mode_tool_builtin():
    script = """
msg = tools.echo(text="programmatic execution active")
print(f"Output: {msg}")
result = 42
"""
    output = code_mode_tool.invoke({"code": script})
    assert "Programmatic Tool Invocations" in output
    assert "Output: programmatic execution active" in output
    assert "Final Result: 42" in output
