"""Atomic patch application engine for dynamic workflow task graphs."""

from __future__ import annotations

import copy

from alpha.workflow.events import get_event_dispatcher
from alpha.workflow.models import (
    NodeStatus,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
)
from alpha.workflow.patch_validator import PatchValidationResult, PatchValidator


class WorkflowPatchEngine:
    """Manages dry-run simulation and atomic commits of workflow patches."""

    def __init__(self, validator: PatchValidator | None = None) -> None:
        self.validator = validator or PatchValidator()
        self.events = get_event_dispatcher()

    def simulate(self, graph: WorkflowGraph, patch: WorkflowPatch) -> PatchValidationResult:
        """Simulate a patch without mutating the original graph."""
        return self.validator.validate(graph, patch)

    def apply(self, run: WorkflowRun, graph: WorkflowGraph, patch: WorkflowPatch) -> tuple[WorkflowGraph, PatchValidationResult]:
        """Validate and apply a patch atomically to produce an incremented WorkflowGraph."""
        if patch.workflow_run_id != run.run_id:
            validation = PatchValidationResult(
                allowed=False,
                reason=f"patch targets run '{patch.workflow_run_id}' but engine is executing '{run.run_id}'",
            )
            self.events.emit(
                "patch_rejected",
                run.run_id,
                reason=validation.reason,
                patch=patch.model_dump(),
            )
            return graph, validation
        validation = self.validator.validate(graph, patch)
        if not validation.allowed:
            self.events.emit(
                "patch_rejected",
                run.run_id,
                reason=validation.reason,
                patch=patch.model_dump(),
            )
            return graph, validation

        # Clone and mutate
        new_nodes = copy.deepcopy(graph.nodes)
        new_edges = copy.deepcopy(graph.edges)

        for op in patch.operations:
            kind = op.op
            args = op.args

            if kind == "add_node":
                node_data = args["node"]
                node = WorkflowNode(**node_data) if isinstance(node_data, dict) else node_data
                new_nodes[node.id] = node

            elif kind == "remove_node":
                node_id = args["node_id"]
                if node_id in new_nodes:
                    del new_nodes[node_id]
                new_edges = [e for e in new_edges if e.source != node_id and e.target != node_id]

            elif kind == "replace_node":
                node_id = args["node_id"]
                new_node_data = args["new_node"]
                new_node = WorkflowNode(**new_node_data) if isinstance(new_node_data, dict) else new_node_data
                new_nodes[node_id] = new_node

            elif kind == "update_node_config":
                node_id = args["node_id"]
                updates = args.get("updates", {})
                if node_id in new_nodes:
                    node = new_nodes[node_id]
                    node.config.update(updates)

            elif kind == "retry_node":
                node_id = args["node_id"]
                if node_id in new_nodes:
                    node = new_nodes[node_id]
                    node.status = NodeStatus.READY
                    node.output = None
                    # A retry is a new attempt boundary; old evidence must not
                    # satisfy the new completion gate or leak into replay.
                    node.evidence = []
                    node.approval_request_id = None
                    node.approval_requested_at = None
                    if node_id in run.failed_nodes:
                        run.failed_nodes.remove(node_id)
                    if node_id in run.completed_nodes:
                        run.completed_nodes.remove(node_id)
                    run.node_states[node_id] = NodeStatus.READY
                    run.metrics.setdefault("retried_nodes", []).append(node_id)

            elif kind == "add_edge":
                edge_data = args["edge"]
                edge = WorkflowEdge(**edge_data) if isinstance(edge_data, dict) else edge_data
                new_edges.append(edge)

            elif kind == "remove_edge":
                src = args.get("source")
                tgt = args.get("target")
                new_edges = [e for e in new_edges if not (e.source == src and e.target == tgt)]

            elif kind == "insert_before":
                target_id = args["target_node_id"]
                new_node_data = args["node"]
                new_node = WorkflowNode(**new_node_data) if isinstance(new_node_data, dict) else new_node_data
                new_nodes[new_node.id] = new_node
                for e in new_edges:
                    if e.target == target_id:
                        e.target = new_node.id
                new_edges.append(WorkflowEdge(source=new_node.id, target=target_id))

            elif kind == "insert_after":
                src_id = args["source_node_id"]
                new_node_data = args["node"]
                new_node = WorkflowNode(**new_node_data) if isinstance(new_node_data, dict) else new_node_data
                new_nodes[new_node.id] = new_node
                for e in new_edges:
                    if e.source == src_id:
                        e.source = new_node.id
                new_edges.append(WorkflowEdge(source=src_id, target=new_node.id))

            elif kind == "create_loop":
                loop_node_id = args["loop_node_id"]
                target_node_id = args["target_node_id"]
                condition = args.get("condition")
                new_edges.append(WorkflowEdge(source=loop_node_id, target=target_node_id, condition=condition))

            elif kind == "set_route":
                src = args["source"]
                target = args["target"]
                condition = args.get("condition")
                # Remove prior normal outgoing edges from src and insert new conditional route
                new_edges = [e for e in new_edges if e.source != src]
                new_edges.append(WorkflowEdge(source=src, target=target, condition=condition))

        new_graph = WorkflowGraph(
            version=graph.version + 1,
            nodes=new_nodes,
            edges=new_edges,
            metadata={**graph.metadata, "last_patch_reason": patch.reason},
        )

        run.graph_version = new_graph.version
        run.patches_applied.append(patch)

        self.events.emit(
            "patch_committed",
            run.run_id,
            graph_version=new_graph.version,
            reason=patch.reason,
            patch=patch.model_dump(),
        )

        return new_graph, validation
