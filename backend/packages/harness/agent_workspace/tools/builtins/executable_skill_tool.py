"""Built-in tool for invoking executable Python skills."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.skills.executable import ExecutableSkillReference, get_skill_runner


@tool("invoke_python_skill", parse_docstring=True)
async def invoke_python_skill_tool(
    import_path: str,
    callable_name: str,
    arguments: str = "{}",
) -> str:
    """Execute a Python-backed skill callable programmatically.

    Inspired by Prime Agent's executable skill design where skills are importable Python
    packages/callables with deterministic execution and verification.

    Args:
        import_path: Python module import path (e.g. 'math' or 'my_skills.validator').
        callable_name: Name of the function/callable in the module to run.
        arguments: JSON-formatted string of keyword arguments to pass to the callable.
    """
    try:
        parsed_args = json.loads(arguments) if arguments.strip() else {}
        if not isinstance(parsed_args, dict):
            return "Error: 'arguments' must be a JSON object (dict)."
    except Exception as e:
        return f"Error parsing arguments JSON: {e}"

    ref = ExecutableSkillReference(
        import_path=import_path,
        callable_name=callable_name,
    )

    runner = get_skill_runner()
    try:
        result = await runner.execute(ref, parsed_args)
        return f"Skill result: {result!r}"
    except Exception as e:
        return f"Error executing Python skill {import_path}:{callable_name}: {e}"
