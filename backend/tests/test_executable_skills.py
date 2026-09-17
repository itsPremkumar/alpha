"""Unit tests for Executable Python Skills protocol and runner."""

import pytest

from agent_workspace.skills.executable import (
    ExecutableSkillReference,
    ExecutableSkillRunner,
)
from agent_workspace.tools.builtins.executable_skill_tool import invoke_python_skill_tool


def sample_sync_calculator(a: int, b: int, op: str = "add") -> int:
    if op == "add":
        return a + b
    elif op == "mul":
        return a * b
    return 0


async def sample_async_validator(name: str) -> dict[str, str]:
    return {"validated": True, "target": name.upper()}


@pytest.mark.asyncio
async def test_executable_skill_runner_sync():
    runner = ExecutableSkillRunner()
    ref = ExecutableSkillReference(
        import_path="test_executable_skills",
        callable_name="sample_sync_calculator",
    )

    res_add = await runner.execute(ref, {"a": 15, "b": 25, "op": "add"})
    assert res_add == 40

    res_mul = await runner.execute(ref, {"a": 6, "b": 7, "op": "mul"})
    assert res_mul == 42


@pytest.mark.asyncio
async def test_executable_skill_runner_async():
    runner = ExecutableSkillRunner()
    ref = ExecutableSkillReference(
        import_path="test_executable_skills",
        callable_name="sample_async_validator",
    )

    res = await runner.execute(ref, {"name": "deerflow"})
    assert res == {"validated": True, "target": "DEERFLOW"}


def test_executable_skill_reference_validation():
    with pytest.raises(ValueError, match="Skill reference requires 'import'"):
        ExecutableSkillReference.from_dict({"type": "python", "callable": "foo"})

    with pytest.raises(ValueError, match="Skill reference requires 'callable'"):
        ExecutableSkillReference.from_dict({"type": "python", "import": "math"})

    valid = ExecutableSkillReference.from_dict({
        "type": "python",
        "import": "math",
        "callable": "sqrt",
    })
    assert valid.import_path == "math"
    assert valid.callable_name == "sqrt"


@pytest.mark.asyncio
async def test_invoke_python_skill_tool():
    res = await invoke_python_skill_tool.ainvoke({
        "import_path": "math",
        "callable_name": "isqrt",
        "arguments": '{"n": 144}',
    })
    assert "12" in res
