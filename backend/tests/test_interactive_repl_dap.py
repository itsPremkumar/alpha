"""Tests for Interactive Runtime REPL and DAP Engine."""

from alpha.debugging.interactive_repl_dap import InteractiveDebugEngine


def test_debug_session_breakpoints_and_frame_inspection():
    engine = InteractiveDebugEngine()
    session = engine.create_session("sess_test_1")

    # Code with an intentional breakpoint
    sample_code = """
x = 10
y = 20
z = x + y
"""
    # Break at line 4 (z = x + y)
    session.add_breakpoint("<test_debug>", 4)

    result = engine.execute_with_breakpoints("sess_test_1", sample_code, filename="<test_debug>")
    assert result["paused"] is True
    assert "Breakpoint hit" in result["pause_reason"]
    assert result["current_frame"] is not None
    assert result["current_frame"]["line_number"] == 4
    assert result["current_frame"]["locals_dict"]["x"] == "10"
    assert result["current_frame"]["locals_dict"]["y"] == "20"


def test_debug_session_conditional_breakpoint():
    engine = InteractiveDebugEngine()
    session = engine.create_session("sess_test_2")

    code = """
total = 0
for i in range(5):
    total += i
"""
    # Break only when total == 3
    session.add_breakpoint("<test_cond>", 4, condition="total == 3")

    result = engine.execute_with_breakpoints("sess_test_2", code, filename="<test_cond>")
    assert result["paused"] is True
    assert result["current_frame"]["locals_dict"]["total"] == "3"
