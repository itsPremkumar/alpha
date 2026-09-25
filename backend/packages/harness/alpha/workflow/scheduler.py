"""Dynamic admission and priority scheduler for workflow task graphs."""

from __future__ import annotations

from alpha.workflow.expressions import evaluate_condition
from alpha.workflow.models import EdgeMode, NodeStatus, WorkflowGraph, WorkflowRun


class WriteScopeCollisionError(ValueError):
    """Raised when two nodes in the same parallel execution wave share overlapping write paths."""

    pass


def _scope_overlap(left: str, right: str) -> bool:
    """Return whether two write scopes are equal or nested."""
    a = left.replace("\\", "/").strip("/").lower()
    b = right.replace("\\", "/").strip("/").lower()
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


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

            # Check incoming edges.  Conditional edges from the same source
            # are alternatives (OR), while edges from different sources form
            # a join (AND).  The previous implementation treated every
            # incoming edge as an AND, which meant a normal ``pass`` and a
            # conditional ``fail`` branch blocked each other after a router
            # chose one outcome.
            incoming = graph.incoming_edges(nid)
            if not incoming:
                ready.append(nid)
                continue

            by_source: dict[str, list] = {}
            for edge in incoming:
                by_source.setdefault(edge.source, []).append(edge)

            context = {"state": run.state, "metrics": run.metrics}
            edge_deps_satisfied = True
            for source, source_edges in by_source.items():
                if source not in completed:
                    edge_deps_satisfied = False
                    break

                # BARRIER is deliberately conjunctive within a source group.
                # NORMAL/CONDITIONAL edges are alternatives unless a barrier
                # is present, which lets routers express one-of branches
                # without weakening multi-source joins.
                if any(edge.mode is EdgeMode.BARRIER for edge in source_edges):
                    group_satisfied = True
                    for edge in source_edges:
                        if edge.condition and not evaluate_condition(edge.condition, context):
                            group_satisfied = False
                            break
                else:
                    group_satisfied = False
                    for edge in source_edges:
                        if edge.condition is None or evaluate_condition(edge.condition, context):
                            group_satisfied = True
                            break

                if not group_satisfied:
                    edge_deps_satisfied = False
                    break

            if edge_deps_satisfied:
                ready.append(nid)

        # Sort by priority
        ready.sort(key=lambda x: graph.nodes[x].config.get("priority", 0), reverse=True)
        return ready

    def unselected_branch_nodes(self, graph: WorkflowGraph, run: WorkflowRun) -> list[str]:
        """Return pending nodes whose completed conditional alternatives lost.

        A router commonly emits mutually exclusive edges from one source.  Once
        that source is complete, an all-false alternative group is a proven
        branch skip rather than a deadlock.  Nodes with an unfinished source
        are never skipped here.
        """
        completed = set(run.completed_nodes)
        context = {"state": run.state, "metrics": run.metrics}
        skipped: list[str] = []
        for nid, node in graph.nodes.items():
            status = run.node_states.get(nid, node.status)
            if status not in (NodeStatus.PENDING, NodeStatus.READY):
                continue
            if not all(dep in completed for dep in node.depends_on):
                continue
            incoming = graph.incoming_edges(nid)
            if not incoming:
                continue
            by_source: dict[str, list] = {}
            for edge in incoming:
                by_source.setdefault(edge.source, []).append(edge)
            all_sources_complete = True
            all_groups_lost = True
            for source, edges in by_source.items():
                if source not in completed:
                    all_sources_complete = False
                    break
                if any(edge.mode is EdgeMode.BARRIER for edge in edges):
                    group_lost = all(bool(edge.condition) and not evaluate_condition(edge.condition, context) for edge in edges)
                else:
                    group_lost = all(edge.condition is not None and not evaluate_condition(edge.condition, context) for edge in edges)
                if not group_lost:
                    all_groups_lost = False
                    break
            if all_sources_complete and all_groups_lost:
                skipped.append(nid)
        return skipped

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
                    if any(_scope_overlap(scope, existing) for existing in seen_scopes):
                        collision = True
                        break

                if not collision:
                    current_wave.append(nid)
                    for scope in node.write_scope:
                        seen_scopes[scope] = nid
                    remaining.remove(nid)

            if not current_wave:
                # If everything collided with each other, pop the first one sequentially
                current_wave.append(remaining.pop(0))

            waves.append(current_wave)

        return waves
