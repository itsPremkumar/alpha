"""Dynamic admission and priority scheduler for workflow task graphs."""

from __future__ import annotations

from alpha.workflow.expressions import evaluate_condition
from alpha.workflow.models import NodeStatus, WorkflowGraph, WorkflowRun


class WriteScopeCollisionError(ValueError):
    """Raised when two nodes in the same parallel execution wave share overlapping write paths."""
    pass


class WorkflowScheduler:
    """Computes ready nodes, resolves conditional edges, and packs parallel execution waves."""

    def compute_ready_nodes(self, graph: WorkflowGraph, run: WorkflowRun) -> list[str]:
        ready: list[str] = []
        completed = set(run.completed_nodes)

        for nid, node in graph.nodes.items():
            current_status = run.node_states.get(nid, node.status)
            # Compensation nodes only execute when explicitly triggered
            if node.type == "compensation":
                if current_status != NodeStatus.COMPENSATING:
                    continue
            elif current_status not in (NodeStatus.PENDING, NodeStatus.READY):
                continue

            # Check explicit depends_on
            deps_met = all(dep in completed for dep in node.depends_on)
            if not deps_met:
                continue

            # Check incoming edges
            incoming = graph.incoming_edges(nid)
            if not incoming:
                ready.append(nid)
                continue

            # Evaluate incoming edge conditions against current state
            edge_deps_satisfied = True
            for edge in incoming:
                if edge.source not in completed:
                    edge_deps_satisfied = False
                    break

                if edge.condition:
                    context = {"state": run.state, "metrics": run.metrics}
                    if not evaluate_condition(edge.condition, context):
                        edge_deps_satisfied = False
                        break

            if edge_deps_satisfied:
                ready.append(nid)

        # Sort by priority
        ready.sort(key=lambda x: graph.nodes[x].config.get("priority", 0), reverse=True)
        return ready

    def partition_into_waves(self, graph: WorkflowGraph, ready_node_ids: list[str]) -> list[list[str]]:
        """Partition ready nodes into parallel execution waves with disjoint write scopes."""
        waves: list[list[str]] = []
        remaining = list(ready_node_ids)

        while remaining:
            current_wave: list[str] = []
            seen_scopes: dict[str, str] = {}

            for nid in list(remaining):
                node = graph.nodes[nid]
                collision = False
                for scope in node.write_scope:
                    norm = scope.replace("\\", "/").rstrip("/").lower()
                    if norm in seen_scopes:
                        collision = True
                        break

                if not collision:
                    current_wave.append(nid)
                    for scope in node.write_scope:
                        norm = scope.replace("\\", "/").rstrip("/").lower()
                        seen_scopes[norm] = nid
                    remaining.remove(nid)

            if not current_wave:
                # If everything collided with each other, pop the first one sequentially
                current_wave.append(remaining.pop(0))

            waves.append(current_wave)

        return waves
