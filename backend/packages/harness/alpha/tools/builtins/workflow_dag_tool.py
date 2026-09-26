"""DAG & Dynamic Workflow Tool (mass-ulw / omo-dag / DWE).

Allows the agent to construct, plan, adapt, patch, and verify multi-agent dependency task graphs.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.workflow.dag_engine import DAGEngine, DAGWorkflow, UnverifiedNodeCompletionError
from alpha.workflow.models import (
    NodeStatus,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
)
from alpha.workflow.patch import WorkflowPatchEngine

_GLOBAL_DAG_ENGINE = DAGEngine()
_GLOBAL_PATCH_ENGINE = WorkflowPatchEngine()

#: Operations WorkflowPatchEngine.apply() genuinely mutates the graph for
#: (the branches that exist in alpha/workflow/patch.py). The patch validator
#: silently ALLOWS any op it has no branch for, and apply() silently drops it,
#: so anything outside this set must be rejected by the tool instead of being
#: reported as a commit that never happened.
_PATCH_ENGINE_OPS = frozenset(
    {
        "add_node",
        "remove_node",
        "replace_node",
        "update_node_config",
        "add_edge",
        "remove_edge",
        "insert_before",
        "insert_after",
        "create_loop",
        "set_route",
        "retry_node",
    }
)

#: Ops the read-only patch engine does NOT implement but this tool applies
#: itself at the call site (see apply_patch).
_TOOL_APPLIED_OPS = frozenset({"update_edge_condition"})

#: Per-workflow graph version bookkeeping. Legacy DAGWorkflow has no version
#: field, and the tool previously built the base graph with whatever version
#: the caller claimed in the patch, which made the validator's optimistic
#: concurrency check vacuous. Keyed by workflow key; in-process only.
_GRAPH_VERSIONS: dict[str, int] = {}


def _to_node_status(value: str) -> NodeStatus:
    """Map a legacy DAGNode status string onto the graph's NodeStatus enum."""
    try:
        return NodeStatus(value)
    except ValueError:
        # Legacy-only labels have no enum member ('completed' predates
        # SUCCEEDED; gate/budget labels are uppercase). This graph-side copy is
        # advisory only - sync-back never writes status back - so only the
        # completion state needs an exact translation.
        if value == "completed":
            return NodeStatus.SUCCEEDED
        return NodeStatus.PENDING


def _build_base_graph(wf: DAGWorkflow, base_version: int) -> WorkflowGraph:
    """Snapshot the legacy workflow into a patchable WorkflowGraph.

    Crucially, the base graph includes the workflow's EXISTING edges: legacy
    DAGWorkflow stores ordering only as per-node ``depends_on``, so every
    dependency is converted into a concrete WorkflowEdge. (Before this fix the
    base graph was built edgeless, so remove_edge / insert_before / insert_after
    / condition patches all operated on an empty edge list and their results
    were silently lost.)

    Dependencies are represented ONLY as edges here - ``WorkflowNode.depends_on``
    stays empty - so ``remove_edge`` cannot be resurrected from the duplicate
    node-level field on the next round-trip. Graph and legacy stay one-to-one:
    get_dependencies()/get_executable_waves() union both sources, so semantics
    are unchanged.
    """
    graph_nodes: dict[str, WorkflowNode] = {}
    base_edges: list[WorkflowEdge] = []
    for nid, node in wf.nodes.items():
        graph_nodes[nid] = WorkflowNode(
            id=nid,
            prompt=node.prompt,
            category=node.category,
            write_scope=list(node.write_scope),
            status=_to_node_status(node.status),
        )
        for dep in node.depends_on:
            base_edges.append(WorkflowEdge(source=dep, target=nid))
    return WorkflowGraph(version=base_version, nodes=graph_nodes, edges=base_edges)


def _sync_graph_to_legacy(wf: DAGWorkflow, graph: WorkflowGraph) -> None:
    """Mirror the patched WorkflowGraph back onto the legacy DAGWorkflow.

    Syncs node additions/removals AND edge additions/removals/updates:
    legacy topology lives in per-node ``depends_on`` (rebuilt from the graph's
    incoming edges plus any node-level deps), while the full edge records -
    including condition/priority/mode, fields the legacy node model cannot
    hold - are kept verbatim on ``wf.graph_edges``.
    """
    # Nodes removed by the patch leave the legacy workflow too.
    for nid in [nid for nid in wf.nodes if nid not in graph.nodes]:
        del wf.nodes[nid]

    incoming: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
    for edge in graph.edges:
        if edge.target in incoming:
            incoming[edge.target].append(edge.source)

    for nid, node in graph.nodes.items():
        deps = sorted(dict.fromkeys([*(node.depends_on or []), *incoming[nid]]))
        if nid not in wf.nodes:
            wf.add_node(
                node_id=nid,
                prompt=node.prompt or nid,
                category=node.category,
                depends_on=deps,
                write_scope=list(node.write_scope),
                budget=node.budget,
                requires_approval=node.requires_approval,
                gate_timeout_seconds=node.gate_timeout_seconds,
            )
            continue
        legacy = wf.nodes[nid]
        if node.prompt is not None:
            legacy.prompt = node.prompt
        legacy.category = node.category
        legacy.write_scope = list(node.write_scope)
        legacy.depends_on = deps
        legacy.budget = node.budget
        legacy.requires_approval = node.requires_approval
        legacy.gate_timeout_seconds = node.gate_timeout_seconds

    # Full edge records. DAGWorkflow has no edge list or condition field, so
    # the patched edges are preserved as a dynamic attribute on the workflow
    # object (in-process; DAGWorkflow.to_dict()/from_dict() do not carry it).
    wf.graph_edges = [edge.model_dump(mode="json") for edge in graph.edges]


@tool
def workflow_dag_manage(
    action: str,
    key: str = "",
    name: str | None = None,
    node_id: str | None = None,
    prompt: str | None = None,
    category: str = "quick",
    depends_on: list[str] | None = None,
    write_scope: list[str] | None = None,
    evidence: str | None = None,
    output: str | None = None,
    patch_json: str | None = None,
) -> str:
    """Manage dependency-ordered and dynamic task graphs (DAG / DWE).

    Actions include create/add_node/plan_waves/record_evidence/mark_completed/
    status/apply_patch/dynamic_status plus perceive/decompose/boost/auto_execute.
    """
    engine = _GLOBAL_DAG_ENGINE

    if action == "create":
        wf = engine.create_workflow(key, name or key)
        return f"Created workflow '{key}' ({wf.name})."

    if action in ("perceive", "decompose", "boost", "auto_execute"):
        raw_prompt = (prompt or key or "").strip()
        if not raw_prompt:
            return f"Error: prompt is required for action='{action}'."
        from alpha.bots.cloning import get_bot_clone_engine
        from alpha.bots.registry import get_bot_registry
        from alpha.orchestrator.executors import (
            DIGEST_EXECUTOR,
            bind_default_executors,
            get_executor_registry,
        )
        from alpha.skills.hub.discovery import get_skills_hub
        from alpha.skills.mcp_lifecycle import SkillMcpLifecycleManager
        from alpha.workflow.dynamic_assembler import DynamicResourceAssembler
        from alpha.workflow.dynamic_bridge import DynamicWorkflowBridge
        from alpha.workflow.dynamic_decomposer import get_dynamic_decomposer
        from alpha.workflow.dynamic_perception import get_dynamic_perception_engine

        intent = get_dynamic_perception_engine().perceive(raw_prompt)
        if action == "perceive":
            return json.dumps(intent.to_dict(), indent=2)

        goal = get_dynamic_decomposer().decompose(intent, raw_prompt)
        if action == "decompose":
            return json.dumps(goal.to_dict(), indent=2)

        assembler = DynamicResourceAssembler(
            bot_registry=get_bot_registry(),
            clone_engine=get_bot_clone_engine(),
            skills_hub=get_skills_hub(),
            mcp_manager=SkillMcpLifecycleManager(),
        )
        resources = assembler.assemble(goal, raw_prompt)
        bind_default_executors()
        runner = get_executor_registry().build_runner()
        bridge = DynamicWorkflowBridge(
            node_runner=runner,
            compensation_runner=None,
            execution_label="local_digest_projection" if runner is not None else "unbound",
            require_compensation_receipt=True,
        )
        result = bridge.execute_goal(goal, resources, default_executor=DIGEST_EXECUTOR)
        return json.dumps(
            {
                "action": action,
                "run_id": result.run_id,
                "workflow_id": result.workflow_id,
                "status": result.status,
                "total_steps": result.total_steps,
                "completed_nodes": result.completed_nodes,
                "failed_nodes": result.failed_nodes,
                "compensated_nodes": result.compensated_nodes,
                "replans_count": result.replans_count,
                "duration_ms": result.duration_ms,
            },
            indent=2,
        )

    wf = engine.get_workflow(key)
    if not wf:
        return f"Error: workflow '{key}' not found. Use action='create' first."

    if action == "add_node":
        if not node_id or not prompt:
            return "Error: node_id and prompt are required for add_node."
        wf.add_node(
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

    elif action == "apply_patch":
        if not patch_json:
            return "Error: patch_json is required for apply_patch."
        try:
            data = json.loads(patch_json)
            patch = WorkflowPatch(**data)

            # Reject operations neither the patch engine nor this tool can
            # actually execute, instead of reporting a commit that never
            # happened (the validator/apply layer silently skips unknown ops).
            unsupported = [op.op for op in patch.operations if op.op not in _PATCH_ENGINE_OPS and op.op not in _TOOL_APPLIED_OPS]
            if unsupported:
                return f"Error applying patch: unsupported operation(s) {sorted(set(unsupported))} - nothing was applied (no silent no-op commit)."

            # Base graph MUST carry the workflow's existing edges (previously
            # built edgeless), otherwise every edge op round-trips against an
            # empty edge list and its result is silently lost.
            base_graph = _build_base_graph(wf, _GRAPH_VERSIONS.get(wf.key, 1))
            run = WorkflowRun(run_id=f"run_{wf.key}", workflow_id=wf.key, graph_version=base_graph.version)

            new_graph, validation = _GLOBAL_PATCH_ENGINE.apply(run, base_graph, patch)
            if not validation.allowed:
                return f"Patch rejected: {validation.reason}"

            # update_edge_condition has no handler in the (read-only) patch
            # engine: patch_validator allows it without simulating it and
            # WorkflowPatchEngine.apply() drops it. Apply it here so the
            # condition survives the round-trip; an unknown edge reference is
            # an honest error, and the legacy workflow is left untouched.
            for op in patch.operations:
                if op.op != "update_edge_condition":
                    continue
                src = op.args.get("source")
                tgt = op.args.get("target")
                match = next((e for e in new_graph.edges if e.source == src and e.target == tgt), None)
                if match is None:
                    return f"Error applying patch: update_edge_condition: edge '{src}' -> '{tgt}' does not exist in the patched graph."
                match.condition = op.args.get("condition")

            # Sync node AND edge changes back onto the legacy workflow object
            # (previously only brand-new nodes were synced; edge ops - and node
            # removals/updates - never reached the legacy graph).
            _sync_graph_to_legacy(wf, new_graph)
            _GRAPH_VERSIONS[wf.key] = new_graph.version
            return f"Patch successfully committed. New graph version: {new_graph.version}. Total nodes: {len(new_graph.nodes)}. Total edges: {len(new_graph.edges)}."
        except Exception as e:
            return f"Error applying patch: {e}"

    elif action == "dynamic_status":
        nodes_dict = {
            nid: {
                "status": n.status,
                "category": n.category,
                "depends_on": n.depends_on,
                "write_scope": n.write_scope,
                "evidence": n.evidence,
            }
            for nid, n in wf.nodes.items()
        }
        return json.dumps({"workflow": wf.key, "nodes": nodes_dict}, indent=2)

    return f"Error: unknown action '{action}'."
