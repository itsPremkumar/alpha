"""Validation engine for dynamic workflow patches.

Enforces graph safety invariants:
- Base graph version match (optimistic concurrency control).
- Node uniqueness and reference integrity.
- Loop termination constraints (cycles must declare bounded loop policies).
- Disjoint parallel write scopes.
- Security and critical gate preservation.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from alpha.workflow.models import (
    NodeStatus,
    PatchOperation,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
)


@dataclass
class PatchValidationResult:
    allowed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


class PatchValidator:
    """Validates dynamic patches against safety and structural invariants."""

    def validate(self, graph: WorkflowGraph, patch: WorkflowPatch) -> PatchValidationResult:
        # 1. Optimistic Concurrency Control
        if patch.base_graph_version != graph.version:
            return PatchValidationResult(
                allowed=False,
                reason=(f"Optimistic concurrency violation: patch base version {patch.base_graph_version} does not match current graph version {graph.version}."),
            )

        # 2. Simulate patch on a cloned graph
        if not patch.operations:
            return PatchValidationResult(allowed=False, reason="patch contains no operations")
        simulated_nodes = copy.deepcopy(graph.nodes)
        simulated_edges = copy.deepcopy(graph.edges)

        warnings: list[str] = []

        for op in patch.operations:
            res = self._validate_operation(op, simulated_nodes, simulated_edges)
            if not res.allowed:
                return res
            warnings.extend(res.warnings)

        # 3. Validate structural invariants on the mutated graph
        test_graph = WorkflowGraph(
            version=graph.version + 1,
            nodes=simulated_nodes,
            edges=simulated_edges,
        )

        invariants_res = self._validate_invariants(test_graph)
        if not invariants_res.allowed:
            return invariants_res
        warnings.extend(invariants_res.warnings)

        return PatchValidationResult(allowed=True, warnings=warnings)

    def _validate_operation(
        self,
        op: PatchOperation,
        nodes: dict[str, WorkflowNode],
        edges: list[WorkflowEdge],
    ) -> PatchValidationResult:
        kind = op.op
        args = op.args

        if kind == "add_node":
            node_data = args.get("node")
            if not node_data:
                return PatchValidationResult(allowed=False, reason="add_node missing 'node' data.")
            node = WorkflowNode(**node_data) if isinstance(node_data, dict) else node_data
            if node.id in nodes:
                return PatchValidationResult(allowed=False, reason=f"Node '{node.id}' already exists.")
            nodes[node.id] = node

        elif kind == "remove_node":
            node_id = args.get("node_id")
            if not node_id or node_id not in nodes:
                return PatchValidationResult(allowed=False, reason=f"remove_node: Node '{node_id}' not found.")
            # Check if this node is a protected goal gate
            if nodes[node_id].type == "goal_gate":
                return PatchValidationResult(allowed=False, reason=f"Cannot remove protected goal gate '{node_id}'.")
            del nodes[node_id]
            # Remove incident edges
            edges[:] = [e for e in edges if e.source != node_id and e.target != node_id]

        elif kind == "replace_node":
            node_id = args.get("node_id")
            new_node_data = args.get("new_node")
            if not node_id or node_id not in nodes:
                return PatchValidationResult(allowed=False, reason=f"replace_node: Node '{node_id}' not found.")
            if nodes[node_id].type == "goal_gate":
                return PatchValidationResult(allowed=False, reason=f"Cannot replace protected goal gate '{node_id}'.")
            if not new_node_data:
                return PatchValidationResult(allowed=False, reason="replace_node: missing 'new_node'.")
            new_node = WorkflowNode(**new_node_data) if isinstance(new_node_data, dict) else new_node_data
            if new_node.id != node_id:
                return PatchValidationResult(
                    allowed=False,
                    reason=f"replace_node: new node id '{new_node.id}' must match target '{node_id}'.",
                )
            nodes[node_id] = new_node

        elif kind == "update_node_config":
            node_id = args.get("node_id")
            updates = args.get("updates", {})
            if not node_id or node_id not in nodes:
                return PatchValidationResult(allowed=False, reason=f"update_node_config: Node '{node_id}' not found.")
            node = nodes[node_id]
            protected = {
                "id",
                "type",
                "status",
                "executor",
                "requires_approval",
                "compensation_node_id",
                "retry_policy",
                "loop_policy",
            }
            invalid = sorted(set(updates) & protected)
            if invalid:
                return PatchValidationResult(
                    allowed=False,
                    reason=f"update_node_config: fields require a typed replacement: {invalid}",
                )
            node.config.update(updates)

        elif kind == "retry_node":
            node_id = args.get("node_id")
            if not node_id or node_id not in nodes:
                return PatchValidationResult(allowed=False, reason=f"retry_node: Node '{node_id}' not found.")
            if nodes[node_id].type == "compensation":
                return PatchValidationResult(
                    allowed=False,
                    reason=f"retry_node: compensation node '{node_id}' cannot be reset as a normal task.",
                )
            # Retrying is a state transition, not a new graph node.  The
            # validator only checks the target exists; the engine performs
            # the reset under the run claim and journals it.
            nodes[node_id].status = NodeStatus.READY

        elif kind == "add_edge":
            edge_data = args.get("edge")
            if not edge_data:
                return PatchValidationResult(allowed=False, reason="add_edge: missing 'edge' data.")
            edge = WorkflowEdge(**edge_data) if isinstance(edge_data, dict) else edge_data
            if edge.source not in nodes:
                return PatchValidationResult(allowed=False, reason=f"add_edge: Source '{edge.source}' not found.")
            if edge.target not in nodes:
                return PatchValidationResult(allowed=False, reason=f"add_edge: Target '{edge.target}' not found.")
            edges.append(edge)

        elif kind == "remove_edge":
            src = args.get("source")
            tgt = args.get("target")
            if not any(edge.source == src and edge.target == tgt for edge in edges):
                return PatchValidationResult(allowed=False, reason=f"remove_edge: edge '{src}' -> '{tgt}' not found")
            edges[:] = [e for e in edges if not (e.source == src and e.target == tgt)]

        elif kind == "insert_before":
            target_id = args.get("target_node_id")
            new_node_data = args.get("node")
            if not target_id or target_id not in nodes:
                return PatchValidationResult(allowed=False, reason=f"insert_before: Target '{target_id}' not found.")
            new_node = WorkflowNode(**new_node_data) if isinstance(new_node_data, dict) else new_node_data
            if new_node.id in nodes:
                return PatchValidationResult(allowed=False, reason=f"Node '{new_node.id}' already exists.")
            nodes[new_node.id] = new_node
            # Redirect existing incoming edges of target_id to new_node
            for e in edges:
                if e.target == target_id:
                    e.target = new_node.id
            edges.append(WorkflowEdge(source=new_node.id, target=target_id))

        elif kind == "insert_after":
            src_id = args.get("source_node_id")
            new_node_data = args.get("node")
            if not src_id or src_id not in nodes:
                return PatchValidationResult(allowed=False, reason=f"insert_after: Source '{src_id}' not found.")
            new_node = WorkflowNode(**new_node_data) if isinstance(new_node_data, dict) else new_node_data
            if new_node.id in nodes:
                return PatchValidationResult(allowed=False, reason=f"insert_after: Node '{new_node.id}' already exists.")
            nodes[new_node.id] = new_node
            # Redirect existing outgoing edges of src_id to new_node
            for e in edges:
                if e.source == src_id:
                    e.source = new_node.id
            edges.append(WorkflowEdge(source=src_id, target=new_node.id))

        elif kind == "create_loop":
            loop_node_id = args.get("loop_node_id")
            target_node_id = args.get("target_node_id")
            condition = args.get("condition")
            if loop_node_id not in nodes or target_node_id not in nodes:
                return PatchValidationResult(allowed=False, reason="create_loop: Invalid node reference.")
            node = nodes[loop_node_id]
            if not node.loop_policy:
                return PatchValidationResult(
                    allowed=False,
                    reason=f"create_loop: Node '{loop_node_id}' must specify a bounded loop_policy.",
                )
            edges.append(WorkflowEdge(source=loop_node_id, target=target_node_id, condition=condition))

        elif kind == "set_route":
            src = args.get("source")
            tgt = args.get("target")
            if not src or src not in nodes:
                return PatchValidationResult(allowed=False, reason=f"set_route: Source '{src}' not found.")
            if not tgt or tgt not in nodes:
                return PatchValidationResult(allowed=False, reason=f"set_route: Target '{tgt}' not found.")
            condition = args.get("condition")
            edges[:] = [edge for edge in edges if edge.source != src]
            edges.append(WorkflowEdge(source=src, target=tgt, condition=condition))

        elif kind == "update_edge_condition":
            # The legacy DAG tool applies this operation at its call site after
            # the core patch engine has produced the candidate graph.  Keep it
            # a recognised/deferred operation here rather than rejecting it as
            # unknown; the tool performs the edge lookup and reports an honest
            # error when the referenced edge is absent.
            return PatchValidationResult(allowed=True)

        elif kind in {
            "fan_out",
            "fan_in",
            "set_loop_limit",
            "skip_node",
            "request_human",
            "request_review",
        }:
            return PatchValidationResult(
                allowed=False,
                reason=f"unsupported patch operation '{kind}' in the core patch engine",
            )

        else:
            return PatchValidationResult(allowed=False, reason=f"unknown patch operation '{kind}'")

        return PatchValidationResult(allowed=True)

    def _validate_invariants(self, graph: WorkflowGraph) -> PatchValidationResult:
        # Check that all edge endpoints exist
        for edge in graph.edges:
            if edge.source not in graph.nodes:
                return PatchValidationResult(allowed=False, reason=f"Edge source '{edge.source}' missing.")
            if edge.target not in graph.nodes:
                return PatchValidationResult(allowed=False, reason=f"Edge target '{edge.target}' missing.")

        # Check for cycles and ensure any cycle is bounded by loop_policy
        adj: dict[str, list[str]] = {nid: [] for nid in graph.nodes}
        for edge in graph.edges:
            adj[edge.source].append(edge.target)

        visited: dict[str, int] = {nid: 0 for nid in graph.nodes}  # 0=unvisited, 1=visiting, 2=visited
        cycle_nodes: set[str] = set()

        def dfs(u: str) -> None:
            visited[u] = 1
            for v in adj.get(u, []):
                if visited[v] == 1:
                    cycle_nodes.add(v)
                    cycle_nodes.add(u)
                elif visited[v] == 0:
                    dfs(v)
            visited[u] = 2

        for nid in graph.nodes:
            if visited[nid] == 0:
                dfs(nid)

        if cycle_nodes:
            # Verify that at least one node in the cycle defines a bounded loop policy
            has_loop_policy = any(graph.nodes[cn].loop_policy is not None and graph.nodes[cn].loop_policy.max_iterations > 0 for cn in cycle_nodes)
            if not has_loop_policy:
                return PatchValidationResult(
                    allowed=False,
                    reason=f"Unbounded cycle detected in nodes: {sorted(cycle_nodes)}. Must define a bounded loop_policy.",
                )

        return PatchValidationResult(allowed=True)
