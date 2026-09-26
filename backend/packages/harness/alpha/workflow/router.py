"""Dynamic router for state-driven branching and edge selection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from alpha.workflow.expressions import evaluate_condition_strict
from alpha.workflow.models import WorkflowGraph, WorkflowRun


class RouteDecision(BaseModel):
    decision: str = "route"
    target: str
    reason: str
    confidence: float = 1.0
    signals: dict[str, Any] = Field(default_factory=dict)


class DynamicRouter:
    """Evaluates dynamic routing logic for router and conditional nodes."""

    def resolve_next_nodes(
        self,
        node_id: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
    ) -> list[RouteDecision]:
        outgoing = graph.outgoing_edges(node_id)
        if not outgoing:
            return []

        context = {"state": run.state, "metrics": run.metrics}
        decisions: list[RouteDecision] = []

        # Evaluate conditional edges
        for edge in outgoing:
            if edge.condition:
                if evaluate_condition_strict(edge.condition, context):
                    decisions.append(
                        RouteDecision(
                            target=edge.target,
                            reason=f"Condition '{edge.condition}' matched state.",
                            confidence=1.0,
                            signals={"matched_condition": edge.condition},
                        )
                    )
            else:
                # Normal unconditional edge
                decisions.append(
                    RouteDecision(
                        target=edge.target,
                        reason="Unconditional edge transition.",
                        confidence=1.0,
                    )
                )

        return decisions
