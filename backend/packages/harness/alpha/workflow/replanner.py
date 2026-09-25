"""Runtime replanner proposing dynamic graph adaptations upon failure or stagnation."""

from __future__ import annotations

from alpha.workflow.models import (
    NodeType,
    PatchOperation,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
)


class RuntimeReplanner:
    """Detects execution roadblocks and synthesizes typed patches to adapt the workflow."""

    def propose_failure_repair_patch(
        self,
        failed_node_id: str,
        error_message: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
    ) -> WorkflowPatch:
        """Create a patch inserting a diagnostic & repair node before retrying or escalating."""
        repair_node_id = f"repair_{failed_node_id}_{run.graph_version}"
        repair_node = WorkflowNode(
            id=repair_node_id,
            type=NodeType.AGENT,
            executor="alpha.agent",
            prompt=f"Diagnose and remediate failure in '{failed_node_id}': {error_message}",
            category="repair",
            config={"target_failed_node": failed_node_id, "error": error_message},
        )

        ops = [
            # ``insert_before`` is the atomic graph mutation; adding the same
            # node in a separate operation was redundant and made replay
            # depend on operation ordering.
            PatchOperation(
                op="insert_before",
                args={"target_node_id": failed_node_id, "node": repair_node.model_dump()},
            ),
            # A repair node alone cannot make the failed target schedulable
            # again.  Reset that exact target through a typed operation; the
            # engine removes its failed marker under the run claim.
            PatchOperation(
                op="retry_node",
                args={"node_id": failed_node_id},
            ),
        ]

        return WorkflowPatch(
            workflow_run_id=run.run_id,
            base_graph_version=graph.version,
            reason=f"Auto-repair inserted for failed node '{failed_node_id}': {error_message}",
            proposed_by="runtime_replanner",
            operations=ops,
        )

    def propose_evidence_remediation_patch(
        self,
        node_id: str,
        missing_evidence: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
    ) -> WorkflowPatch:
        """Create a patch to gather additional evidence or verification."""
        research_node_id = f"gather_evidence_{node_id}_{run.graph_version}"
        research_node = WorkflowNode(
            id=research_node_id,
            type=NodeType.TOOL,
            executor="alpha.tool",
            prompt=f"Gather required evidence for '{node_id}': {missing_evidence}",
            category="evidence",
        )

        ops = [
            PatchOperation(
                op="insert_before",
                args={"target_node_id": node_id, "node": research_node.model_dump()},
            ),
            PatchOperation(
                op="retry_node",
                args={"node_id": node_id},
            ),
        ]

        return WorkflowPatch(
            workflow_run_id=run.run_id,
            base_graph_version=graph.version,
            reason=f"Evidence gap detected for node '{node_id}': {missing_evidence}",
            proposed_by="runtime_replanner",
            operations=ops,
        )
