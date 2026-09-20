"""Built-in cognitive_compiler tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.planning.compiler import CognitiveCompiler


@tool("compile_cognitive_plan", parse_docstring=True)
def compile_cognitive_plan(
    goal: str,
    risk_tier: str = "R1",
    task_dag_json: str = "",
    strict_validation: bool = True,
) -> str:
    """Compile a mission using the P0-P21 Cognitive Compiler.

    Generates competing Strategy Candidates (A/B/C) with trade-off scoring,
    topological parallel execution waves, and pre-computed recovery fallback trees.

    Args:
        goal: The mission objective to compile.
        risk_tier: The risk tier classification (R0 to R6).
        task_dag_json: Optional JSON string mapping task_id to list of dependency task_ids.
        strict_validation: When true, invalid DAGs (cycles/unknown refs) return an error payload instead of an emergency wave.
    """
    dag = None
    if task_dag_json:
        try:
            dag = json.loads(task_dag_json)
        except Exception:
            return json.dumps({"error": "task_dag_json is not valid JSON"}, indent=2)

    compiler = CognitiveCompiler()
    try:
        ir = compiler.compile(goal=goal, task_dag=dag, risk_tier=risk_tier, strict_validation=strict_validation)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, indent=2)

    return json.dumps(ir.to_dict(), indent=2)
