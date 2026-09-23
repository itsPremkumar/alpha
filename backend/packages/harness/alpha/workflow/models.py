"""Typed models and schemas for Alpha Dynamic Workflow Engine (DWE).

Supports adaptive task graph mutation, dynamic branching, fan-out/fan-in,
bounded iterative loops, human-in-the-loop approvals, and durable execution state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class NodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"
    COMPENSATING = "compensating"


class NodeType(StrEnum):
    AGENT = "agent"
    BOT = "bot"
    SUBAGENT = "subagent"
    TOOL = "tool"
    MCP = "mcp"
    ROUTER = "router"
    CONDITION = "condition"
    PARALLEL = "parallel"
    MAP = "map"
    REDUCE = "reduce"
    RACE = "race"
    QUORUM = "quorum"
    LOOP = "loop"
    REVIEW = "review"
    APPROVAL = "approval"
    WAIT = "wait"
    EVENT_WAIT = "event_wait"
    SUBWORKFLOW = "subworkflow"
    CHECKPOINT = "checkpoint"
    COMPENSATION = "compensation"
    GOAL_GATE = "goal_gate"


class EdgeMode(StrEnum):
    NORMAL = "normal"
    CONDITIONAL = "conditional"
    BARRIER = "barrier"


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EVENT = "waiting_event"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"


class RetryPolicy(BaseModel):
    max_attempts: int = 3
    backoff: Literal["fixed", "exponential", "jitter"] = "exponential"
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    retry_on_errors: list[str] = Field(default_factory=lambda: ["transient", "timeout", "rate_limit"])


class LoopPolicy(BaseModel):
    max_iterations: int = 10
    stop_condition: str | None = None
    progress_metric: str | None = None
    min_progress_delta: float = 0.01
    stagnation_limit: int = 3


class WorkflowEdge(BaseModel):
    source: str
    target: str
    condition: str | None = None  # Safe expression e.g. "state.score > 0.8"
    priority: int = 0
    mode: EdgeMode = EdgeMode.NORMAL


class WorkflowNode(BaseModel):
    id: str
    type: NodeType = NodeType.TOOL
    executor: str = "alpha.tool"
    config: dict[str, Any] = Field(default_factory=dict)
    prompt: str | None = None
    category: str = "quick"
    condition: str | None = None
    timeout_seconds: float | None = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    loop_policy: LoopPolicy | None = None
    write_scope: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    approval_request_id: str | None = None
    gate_timeout_seconds: float | None = None
    compensation_node_id: str | None = None
    budget: int | None = None
    tokens_consumed: int = 0
    status: NodeStatus = NodeStatus.PENDING
    evidence: list[str] = Field(default_factory=list)
    output: Any | None = None
    depends_on: list[str] = Field(default_factory=list)


class PatchOperation(BaseModel):
    op: Literal[
        "add_node",
        "remove_node",
        "replace_node",
        "update_node_config",
        "add_edge",
        "remove_edge",
        "update_edge_condition",
        "set_route",
        "insert_before",
        "insert_after",
        "fan_out",
        "fan_in",
        "create_loop",
        "set_loop_limit",
        "retry_node",
        "skip_node",
        "request_human",
        "request_review",
    ]
    args: dict[str, Any] = Field(default_factory=dict)


class WorkflowPatch(BaseModel):
    workflow_run_id: str
    base_graph_version: int
    reason: str
    proposed_by: str = "planner"
    operations: list[PatchOperation] = Field(default_factory=list)
    expected_effects: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WorkflowGraph(BaseModel):
    version: int = 1
    nodes: dict[str, WorkflowNode] = Field(default_factory=dict)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_node(self, node: WorkflowNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Node '{node.id}' already exists in graph.")
        self.nodes[node.id] = node

    def add_edge(self, edge: WorkflowEdge) -> None:
        if edge.source not in self.nodes:
            raise ValueError(f"Source node '{edge.source}' does not exist.")
        if edge.target not in self.nodes:
            raise ValueError(f"Target node '{edge.target}' does not exist.")
        self.edges.append(edge)

    def outgoing_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.source == node_id]

    def incoming_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.target == node_id]

    def get_dependencies(self, node_id: str) -> list[str]:
        deps = set(self.nodes[node_id].depends_on)
        for e in self.incoming_edges(node_id):
            deps.add(e.source)
        return sorted(deps)

    def get_executable_waves(self) -> list[list[str]]:
        """Compute execution waves via Kahn's algorithm."""
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        graph: dict[str, set[str]] = {nid: set() for nid in self.nodes}

        for nid, node in self.nodes.items():
            deps = set(node.depends_on)
            for e in self.incoming_edges(nid):
                deps.add(e.source)
            for dep in deps:
                if dep in self.nodes:
                    graph[dep].add(nid)
                    in_degree[nid] += 1

        waves: list[list[str]] = []
        current_wave = [nid for nid, deg in in_degree.items() if deg == 0]
        processed = 0

        while current_wave:
            waves.append(sorted(current_wave))
            processed += len(current_wave)
            next_wave = []
            for nid in current_wave:
                for dependent in graph[nid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_wave.append(dependent)
            current_wave = next_wave

        if processed != len(self.nodes):
            # Potential cycle or loop edge detected
            pass

        return waves


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    graph_version: int = 1
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    state: dict[str, Any] = Field(default_factory=dict)
    node_states: dict[str, NodeStatus] = Field(default_factory=dict)
    active_nodes: list[str] = Field(default_factory=list)
    completed_nodes: list[str] = Field(default_factory=list)
    failed_nodes: list[str] = Field(default_factory=list)
    waiting_nodes: list[str] = Field(default_factory=list)
    iteration_counts: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)
    patches_applied: list[WorkflowPatch] = Field(default_factory=list)
    waiting_reason: str | None = None
    approval_request_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    graph: WorkflowGraph = Field(default_factory=WorkflowGraph)
    variables: dict[str, Any] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
