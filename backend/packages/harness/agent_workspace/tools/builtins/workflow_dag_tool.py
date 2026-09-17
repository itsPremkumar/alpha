"""DAG Workflow Tool (mass-ulw / omo-dag).

Allows the agent to construct, plan, and verify multi-agent dependency DAGs.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.workflow.dag_engine import DAGEngine, UnverifiedNodeCompletionError

_GLOBAL_DAG_ENGINE = DAGEngine()


@tool
def workflow_dag_manage(
    action: str,
    key: str,
    name: str | None = None,
    node_id: str | None = None,
    prompt: str | None = None,
    category: str = "quick",
    depends_on: list[str] | None = None,
    write_scope: list[str] | None = None,
    evidence: str | None = None,
    output: str | None = None,
) -> str:
    """Manage dependency-ordered task graphs (mass-ulw / DAG). Actions: 'create', 'add_node', 'plan_waves', 'record_evidence', 'mark_completed', 'status'."""
    engine = _GLOBAL_DAG_ENGINE

    if action == "create":
        wf = engine.create_workflow(key, name or key)
        return f"Created workflow '{key}' ({wf.name})."

    wf = engine.get_workflow(key)
    if not wf:
        return f"Error: workflow '{key}' not found. Use action='create' first."

    if action == "add_node":
        if not node_id or not prompt:
            return "Error: node_id and prompt are required for add_node."
        node = wf.add_node(
            node_id=node_id,
            prompt=prompt,
            category=category,
            depends_on=depends_on,
            write_scope=write_scope,
        )
        return f"Added node '{node_id}' [category={category}, depends_on={depends_on}] to '{key}'."

    elif action == "plan_waves":
        try:
            wf.validate_wave_write_scopes()
            waves = wf.get_executable_waves()
            return f"Topological execution waves for '{key}':\n" + json.dumps(waves, indent=2)
        except Exception as e:
            return f"Error planning execution waves: {e}"

    elif action == "record_evidence":
        if not node_id or not evidence:
            return "Error: node_id and evidence are required for record_evidence."
        try:
            wf.record_evidence(node_id, evidence)
            return f"Evidence recorded for node '{node_id}'."
        except Exception as e:
            return f"Error: {e}"

    elif action == "mark_completed":
        if not node_id:
            return "Error: node_id is required."
        try:
            wf.mark_completed(node_id, output=output)
            all_done = wf.is_all_completed()
            return f"Node '{node_id}' completed. Entire DAG finished: {all_done}."
        except UnverifiedNodeCompletionError as e:
            return f"Rejected: {e}"
        except Exception as e:
            return f"Error: {e}"

    elif action == "status":
        waves = wf.get_executable_waves()
        summary = {
            "key": wf.key,
            "name": wf.name,
            "node_count": len(wf.nodes),
            "waves": waves,
            "nodes": {
                nid: {
                    "status": n.status,
                    "category": n.category,
                    "evidence_count": len(n.evidence),
                }
                for nid, n in wf.nodes.items()
            },
        }
        return json.dumps(summary, indent=2)

    return f"Error: unknown action '{action}'."
