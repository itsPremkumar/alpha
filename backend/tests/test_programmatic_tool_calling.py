"""Tests for Programmatic Tool Calling (PTC) Engine."""

import tempfile
from pathlib import Path
from alpha.tools.programmatic_calling import ProgrammaticCallingEngine, MAX_STDOUT_BYTES


def test_programmatic_tool_calling_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = ProgrammaticCallingEngine(workspace_root=tmpdir)

        # Write a test file first
        test_file = Path(tmpdir) / "sample.txt"
        test_file.write_text("Hello from PTC test\nLine 2\nLine 3\n", encoding="utf-8")

        script = """
content = tools.read_file("sample.txt")
print(f"Read content: {content.strip()}")
tools.write_file("output.txt", "Processed: " + content.upper())
"""
        result = engine.execute_script(script)
        assert result.return_code == 0
        assert "Read content: Hello from PTC test" in result.stdout
        assert result.tool_calls_count == 2
        assert not result.stdout_truncated

        out_file = Path(tmpdir) / "output.txt"
        assert out_file.exists()
        assert "Processed: HELLO FROM PTC TEST" in out_file.read_text(encoding="utf-8")


def test_programmatic_tool_calling_stdout_spilling():
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = ProgrammaticCallingEngine(workspace_root=tmpdir)

        # Script that prints 70KB of output (exceeds 50KB limit)
        script = """
for i in range(1500):
    print(f"Log line {i}: " + "X" * 60)
"""
        result = engine.execute_script(script)
        assert result.return_code == 0
        assert result.stdout_truncated
        assert result.spill_path is not None
        assert Path(result.spill_path).exists()
        assert len(result.stdout) < 65000
        assert "bytes truncated" in result.stdout
