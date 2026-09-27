"""Dynamic admission and priority scheduler for workflow task graphs."""

from __future__ import annotations

from typing import Any

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


def satisfied_nodes(run: WorkflowRun) -> set[str]:
    """Nodes whose dependency obligation is discharged.

    ``run.completed_nodes`` is the primary source.  A node the engine marked
    ``SKIPPED`` is ALSO discharged: SKIPPED is only ever set by the proven
    losing-branch rule (``unselected_branch_nodes``), i.e. the run has
    positively established that the node must not execute.  Leaving skipped
    nodes out of the satisfied set made every node that JOINS the two arms of a
    conditional fork permanently unschedulable, so the engine could never reach
    a terminal state and its deadlock-recovery path patched the graph forever.
    ``completed_nodes`` itself is deliberately NOT widened, so handoff and
    compensation still see only genuinely executed work.
    """
    discharged = set(run.completed_nodes)
    discharged.update(nid for nid, status in run.node_states.items() if status == NodeStatus.SKIPPED)
    return discharged


class WorkflowScheduler:
    """Computes ready nodes, resolves conditional edges, and packs parallel execution waves."""

    def compute_ready_nodes(self, graph: WorkflowGraph, run: WorkflowRun) -> list[str]:
        ready: list[str] = []
        completed = satisfied_nodes(run)

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
        are never skipped here, and a source that is itself a proven skip
        counts as finished (``satisfied_nodes``) so a join across both arms of
        a fork can be resolved.
        """
        completed = satisfied_nodes(run)
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

    def find_write_scope_collisions(self, graph: WorkflowGraph, node_ids: list[str]) -> list[dict[str, Any]]:
        """Report every overlapping ``write_scope`` pair inside ``node_ids``.

        :meth:`partition_into_waves` RESOLVES collisions by splitting the colliding
        nodes into separate waves, which is safe but silent: an operator reading
        the event log could not tell that two nodes declaring the same write scope
        had been quietly serialized, nor why their "parallel" work ran in sequence.
        This method is the disclosure half — it names the pairs and the scopes, so
        the engine can journal the reason and the operator can fix the graph
        instead of guessing.

        A collision is reported, never raised, because serializing is the correct
        conservative behaviour. Use :meth:`validate_disjoint_write_scopes` when a
        caller wants the strict, raising form.
        """
        collisions: list[dict[str, Any]] = []
        for index, left in enumerate(node_ids):
            left_node = graph.nodes.get(left)
            if left_node is None:
                continue
            for right in node_ids[index + 1 :]:
                right_node = graph.nodes.get(right)
                if right_node is None:
                    continue
                shared = sorted({(lscope, rscope) for lscope in left_node.write_scope for rscope in right_node.write_scope if _scope_overlap(lscope, rscope)})
                if shared:
                    collisions.append(
                        {
                            "nodes": [left, right],
                            "overlapping_scopes": [list(pair) for pair in shared],
                        }
                    )
        return collisions

    def validate_disjoint_write_scopes(self, graph: WorkflowGraph, node_ids: list[str]) -> None:
        """Raise :class:`WriteScopeCollisionError` if any pair in ``node_ids`` overlaps.

        The strict form, for callers that require a set of nodes to be provably
        safe to run concurrently (a sandbox quota check, a reviewer asserting a
        graph is parallel-safe). ``partition_into_waves`` deliberately does NOT
        call this: serializing a collision is correct there, and failing the wave
        would turn a recoverable scheduling detail into a run failure.
        """
        collisions = self.find_write_scope_collisions(graph, node_ids)
        if not collisions:
            return
        described = "; ".join(f"{collision['nodes'][0]} vs {collision['nodes'][1]} on {collision['overlapping_scopes']}" for collision in collisions)
        raise WriteScopeCollisionError(f"overlapping write scopes among ready nodes: {described}")

    def partition_into_waves(self, graph: WorkflowGraph, ready_node_ids: list[str]) -> list[list[str]]:
        """Partition ready nodes into parallel execution waves with disjoint write scopes.

        A collision is resolved by deferring the later node to the next wave, so
        the returned waves are always internally disjoint and safe to execute
        concurrently. ``find_write_scope_collisions`` reports what that deferral
        cost, so the serialization is disclosed rather than silent.
        """
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
