"""Dynamic Execution Bridge to DynamicWorkflowEngine.

Translates DynamicGoal and AssembledResources into a fully compiled WorkflowDefinition,
registers it with DynamicWorkflowEngine, and orchestrates its execution lifecycle:
- Topological wave scheduling
- Runtime replanning on failure (real RuntimeReplanner API)
- Saga rollback compensation on unrecoverable failure

DY-R3 repairs against the REAL alpha.workflow.models API (models.py is never edited):
- NodeType.TASK does not exist. Task nodes use the per-task NodeType chosen by the
  decomposer (see CATEGORY_NODE_TYPES in dynamic_decomposer.py); saga nodes use the
  real NodeType.COMPENSATION kind with config["target_rollback_node"].
- WorkflowNode has no node_type=/metadata= kwargs: the field is ``type`` and the
  metadata-style payload (assigned_bot, inputs, expected_outputs, verification_*)
  lives in the real ``config`` dict. ``is_compensation`` is a config key too, and
  each target node links to its saga node via the real ``compensation_node_id`` field.
- EdgeMode.ALWAYS does not exist: dependency edges use EdgeMode.NORMAL (the model's
  unconditional mode).
- WorkflowRun has no ``graph``, ``error``, or per-node ``error`` field. The bridge
  keeps its own graph handle (engine.graphs / self._active_graph) and derives honest
  error summaries from run status, waiting_reason, and observed failures.
- NodeStatus.COMPLETED does not exist: completion is NodeStatus.SUCCEEDED, read from
  run.node_states (the engine's source of truth) with the graph node as detail.
- replanner.replan_on_error(...) does not exist: the real API is
  RuntimeReplanner.propose_failure_repair_patch(failed_node_id, error_message, graph, run).
- Executor seam: engine.execute_step(run_id, node_runner=...) is called with the
  bridge's injectable node_runner. When no runner is bound and the engine's legacy
  default-execution path fabricates success (canned "Default verification evidence"
  marker), the bridge REJECTS the fabricated result and fails the run honestly —
  it never records fake success. If a future engine honestly raises/fails instead,
  that honest failure propagates through the same paths.
- Saga compensation executes only through the injectable compensation_runner; with
  none bound the result carries compensated=false and an explicit reason.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.workflow.dynamic_assembler import AssembledResources
from alpha.workflow.dynamic_decomposer import DynamicGoal, SagaCompensation
from alpha.workflow.models import (
    EdgeMode,
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.patch_validator import PatchValidator
from alpha.workflow.replanner import RuntimeReplanner
from alpha.workflow.runtime import DynamicWorkflowEngine

logger = logging.getLogger(__name__)

# Evidence marker the engine's legacy default path stamps when it executes a node
# with no node_runner bound. If we see this on a "successful" node while no runner
# is bound, that success was fabricated — reject it (DY-R3 honesty contract).
_ENGINE_DEFAULT_EVIDENCE_PREFIX = "Default verification evidence for"


@dataclass
class DynamicExecutionResult:
    """Execution telemetry and final state summary of a dynamic workflow run."""

    run_id: str
    goal_id: str
    workflow_id: str
    # "completed" | "failed" | "compensated" | "waiting" | "cancelled"
    # "compensated" is only ever set when saga compensation ACTUALLY executed.
    status: str
    total_steps: int
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    compensated_nodes: list[str] = field(default_factory=list)
    node_outputs: dict[str, Any] = field(default_factory=dict)
    replans_count: int = 0
    duration_ms: float = 0.0
    error_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DynamicWorkflowBridge:
    """Bridges goal decomposition and assembled resources to DynamicWorkflowEngine."""

    def __init__(
        self,
        engine: DynamicWorkflowEngine | None = None,
        replanner: RuntimeReplanner | None = None,
        patch_validator: PatchValidator | None = None,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
        compensation_runner: Callable[[SagaCompensation, WorkflowRun], Any] | None = None,
    ) -> None:
        self.engine = engine or DynamicWorkflowEngine()
        self.replanner = replanner or RuntimeReplanner()
        self.patch_validator = patch_validator or PatchValidator()
        # Injectable executor seams. None means "unbound": no node execution is
        # fabricated, and no compensation is claimed as executed.
        self.node_runner = node_runner
        self.compensation_runner = compensation_runner
        # WorkflowRun has no graph field — the bridge owns its graph handle.
        self._active_graph: WorkflowGraph | None = None
        self._active_run_id: str | None = None

    def build_workflow_definition(
        self,
        goal: DynamicGoal,
        resources: AssembledResources,
    ) -> WorkflowDefinition:
        """Compile a DynamicGoal into a concrete WorkflowDefinition graph."""
        nodes: dict[str, WorkflowNode] = {}
        edges: list[WorkflowEdge] = []

        # 1. Map goal tasks to WorkflowNode (real fields: type=, config=)
        for t in goal.tasks:
            # Assign bot role or specific bot name from assembled resources
            assignee = t.assigned_role
            matched_bot: str | None = None
            if resources.bots:
                # Find matching bot if available
                for bname, bprof in resources.bots.items():
                    if t.assigned_role in bprof.get("capabilities", []):
                        assignee = bname
                        matched_bot = bname
                        break

            node_type = t.node_type
            config: dict[str, Any] = {
                "assigned_bot": assignee,
                "inputs": t.inputs,
                "expected_outputs": t.expected_outputs,
                "verification_cmd": t.verification_cmd,
                "verification_criteria": t.verification_criteria,
            }
            if matched_bot:
                config["bot_name"] = matched_bot
            elif node_type == NodeType.BOT:
                # A BOT node with no bound profile cannot execute honestly:
                # downgrade to AGENT so it requires a real runner instead of
                # falling through to whatever default bot the engine finds.
                node_type = NodeType.AGENT
                config["bot_downgrade_reason"] = (
                    f"no assembled bot carries role '{t.assigned_role}'"
                )

            nodes[t.task_id] = WorkflowNode(
                id=t.task_id,
                type=node_type,
                prompt=f"[{t.category.upper()}] {t.title}: {t.description}",
                category=t.category,
                depends_on=list(t.depends_on),
                write_scope=list(t.write_scope),
                retry_policy=t.retry_policy,
                loop_policy=t.loop_policy,
                config=config,
            )

            # Build edges for dependencies (EdgeMode.NORMAL is the real
            # unconditional member; ALWAYS never existed)
            for dep in t.depends_on:
                edges.append(
                    WorkflowEdge(
                        source=dep,
                        target=t.task_id,
                        mode=EdgeMode.NORMAL,
                    )
                )

        # 2. Add Saga compensation nodes (dormant by default: the scheduler only
        # admits them once a target fails and the engine flips them to
        # COMPENSATING via compensation_node_id). Real NodeType.COMPENSATION with
        # the real config keys the engine reads.
        for comp in goal.saga_compensations:
            comp_node_id = f"comp_{comp.target_task_id}"
            if comp_node_id in nodes:
                continue
            nodes[comp_node_id] = WorkflowNode(
                id=comp_node_id,
                type=NodeType.COMPENSATION,
                prompt=f"[COMPENSATION] {comp.description}",
                category="compensation",
                config={
                    "target_rollback_node": comp.target_task_id,
                    "action_type": comp.action_type,
                    "saga_action_id": comp.action_id,
                    "is_compensation": True,
                },
            )
            if comp.target_task_id in nodes:
                nodes[comp.target_task_id].compensation_node_id = comp_node_id

        graph = WorkflowGraph(version=1, nodes=nodes, edges=edges)

        wf_id = f"wf_{goal.goal_id}"
        definition = WorkflowDefinition(
            id=wf_id,
            name=goal.title,
            description=goal.description,
            graph=graph,
            variables={
                "goal_id": goal.goal_id,
                "intent_type": goal.intent.intent_type.value,
                "model_tier": resources.model_tier,
                "assigned_bots": list(resources.bots.keys()),
                "tools": resources.tools,
            },
            policies={
                "max_steps": 50,
                "max_replans": 3,
                "enable_saga_compensation": True,
            },
        )

        return definition

    def execute_goal(
        self,
        goal: DynamicGoal,
        resources: AssembledResources,
        initial_state: dict[str, Any] | None = None,
        max_steps: int = 40,
    ) -> DynamicExecutionResult:
        """Register and execute the dynamic goal workflow with replanning and saga rollbacks."""
        start_time = time.time()
        definition = self.build_workflow_definition(goal, resources)
        self.engine.register_definition(definition)

        # WorkflowRun has no graph field: keep the handle in bridge state.
        graph = (
            self.engine.graphs.get(f"{definition.id}:v{definition.graph.version}")
            or definition.graph
        )
        self._active_graph = graph

        state = {
            "goal_id": goal.goal_id,
            "prompt": goal.intent.raw_prompt,
            **(initial_state or {}),
        }

        run = self.engine.start_run(definition.id, initial_state=state)
        self._active_run_id = run.run_id
        replans_count = 0
        step_idx = 0

        completed: list[str] = []
        failed: list[str] = []
        compensated: list[str] = []
        outputs: dict[str, Any] = {}
        error_parts: list[str] = []
        compensation_info: dict[str, Any] = {
            "executed": False,
            "compensated_nodes": [],
            "reason": "saga not triggered",
        }

        while step_idx < max_steps and run.status == WorkflowRunStatus.RUNNING:
            step_idx += 1

            try:
                # Step the workflow engine through the injectable executor seam.
                # None is passed through as-is: nothing is fabricated here.
                run = self.engine.execute_step(run.run_id, node_runner=self.node_runner)
            except Exception as exc:
                # An honest engine (no runner bound) raises rather than
                # fabricating results — propagate that honestly.
                error_parts.append(f"execute_step raised: {exc}")
                run.status = WorkflowRunStatus.FAILED
                break

            # Patches may publish a newer graph version — refresh our own handle.
            graph = (
                self.engine.graphs.get(f"{run.workflow_id}:v{run.graph_version}")
                or graph
            )
            self._active_graph = graph

            for nid, node in graph.nodes.items():
                if node.type == NodeType.COMPENSATION:
                    # Saga-owned; never counted as normal task completion.
                    continue
                node_state = run.node_states.get(nid, node.status)

                if node_state == NodeStatus.SUCCEEDED and nid not in completed:
                    # Reject fabricated success: with no runner bound, the
                    # engine's legacy default path stamps canned evidence.
                    if self.node_runner is None and any(
                        isinstance(ev, str) and ev.startswith(_ENGINE_DEFAULT_EVIDENCE_PREFIX)
                        for ev in node.evidence
                    ):
                        failed.append(nid)
                        error_parts.append(
                            f"node '{nid}' succeeded via engine default execution "
                            "with no node_runner bound — fabricated result rejected"
                        )
                        run.status = WorkflowRunStatus.FAILED
                        break
                    completed.append(nid)
                    if node.output is not None:
                        outputs[nid] = node.output
                    else:
                        outputs[nid] = {"status": "succeeded", "output_recorded": False}

                elif node_state == NodeStatus.FAILED and nid not in failed:
                    failed.append(nid)
                    # WorkflowRun/WorkflowNode carry no error string — state the
                    # honest observable reason instead of inventing one.
                    reason = (
                        f"node '{nid}' marked FAILED by the engine "
                        "(no per-node error string recorded on WorkflowRun)"
                    )

                    # Attempt runtime replanning via the REAL replanner API
                    if replans_count < 3:
                        replan_patch = self.replanner.propose_failure_repair_patch(
                            failed_node_id=nid,
                            error_message=reason,
                            graph=graph,
                            run=run,
                        )
                        new_graph, validation = self.engine.apply_patch(
                            run.run_id, replan_patch
                        )
                        if validation.allowed:
                            replans_count += 1
                            graph = new_graph
                            self._active_graph = new_graph
                            logger.info(
                                "Replan patch accepted for failed node '%s'.", nid
                            )
                            continue  # engine decides recovery honestly on next step

                    # Unrecoverable: real saga compensation (or honest decline)
                    error_parts.append(reason)
                    comp_ok, comp_reason = self._execute_saga_compensation(
                        run, graph, goal, completed, compensated, error_parts
                    )
                    compensation_info = {
                        "executed": comp_ok,
                        "compensated_nodes": list(compensated),
                        "reason": comp_reason,
                    }
                    run.status = WorkflowRunStatus.FAILED
                    break

        duration = round((time.time() - start_time) * 1000, 2)

        # Honest final status: "compensated" ONLY when compensation executed.
        if run.status == WorkflowRunStatus.COMPLETED:
            final_status = "completed"
        elif run.status in (
            WorkflowRunStatus.WAITING_APPROVAL,
            WorkflowRunStatus.WAITING_EVENT,
            WorkflowRunStatus.SUSPENDED,
        ):
            final_status = "waiting"
        elif run.status == WorkflowRunStatus.CANCELLED:
            final_status = "cancelled"
        elif compensation_info["executed"]:
            final_status = "compensated"
        else:
            final_status = "failed"

        if final_status == "failed" and run.status == WorkflowRunStatus.RUNNING:
            error_parts.append(
                f"step budget exhausted after {step_idx} steps without completion"
            )

        error_summary = "; ".join(error_parts) if error_parts else None
        if error_summary is None:
            # Fall back to the only reason-like fields WorkflowRun really has.
            if run.waiting_reason:
                error_summary = run.waiting_reason
            elif final_status in ("failed", "waiting", "cancelled"):
                error_summary = (
                    f"run '{run.run_id}' ended with status '{run.status.value}'"
                )

        return DynamicExecutionResult(
            run_id=run.run_id,
            goal_id=goal.goal_id,
            workflow_id=definition.id,
            status=final_status,
            total_steps=step_idx,
            completed_nodes=completed,
            failed_nodes=failed,
            compensated_nodes=compensated,
            node_outputs=outputs,
            replans_count=replans_count,
            duration_ms=duration,
            error_summary=error_summary,
            metadata={
                # Real WorkflowRun field (no run.graph exists)
                "graph_version": run.graph_version,
                "acceptance_passed": final_status == "completed",
                "node_runner_bound": self.node_runner is not None,
                "compensation_runner_bound": self.compensation_runner is not None,
                "compensation": compensation_info,
            },
        )

    def _execute_saga_compensation(
        self,
        run: WorkflowRun,
        graph: WorkflowGraph,
        goal: DynamicGoal,
        completed: list[str],
        compensated: list[str],
        error_parts: list[str],
    ) -> tuple[bool, str]:
        """Execute saga compensations in reverse order through the real seam.

        Returns (executed, reason). With no compensation_runner bound this never
        fabricates rollbacks: it returns (False, honest reason).
        """
        logger.info(
            "Running saga compensation check for failed run '%s'.", run.run_id
        )
        if not goal.saga_compensations:
            return False, "no saga compensations declared"

        if self.compensation_runner is None:
            reason = (
                "no compensation executor bound — saga compensations NOT "
                "executed (compensated=false)"
            )
            error_parts.append(reason)
            logger.warning(reason)
            return False, reason

        executed_ok = True
        notes: list[str] = []
        for comp in reversed(goal.saga_compensations):
            # Only roll back work that actually completed (side effects exist).
            if comp.target_task_id not in completed:
                continue
            comp_node_id = f"comp_{comp.target_task_id}"
            if comp_node_id not in graph.nodes:
                executed_ok = False
                notes.append(f"missing compensation node '{comp_node_id}'")
                continue
            try:
                outcome = self.compensation_runner(comp, run)
            except Exception as exc:
                executed_ok = False
                notes.append(f"compensation '{comp.action_id}' raised: {exc}")
                logger.error("Saga compensation '%s' failed: %s", comp.action_id, exc)
                continue

            cnode = graph.nodes[comp_node_id]
            cnode.status = NodeStatus.SUCCEEDED
            run.node_states[comp_node_id] = NodeStatus.SUCCEEDED
            cnode.output = (
                outcome
                if outcome is not None
                else f"Rolled back {comp.action_type}: {comp.description}"
            )
            cnode.evidence.append(
                f"compensation_runner executed '{comp.action_id}'"
            )
            if comp_node_id not in run.completed_nodes:
                run.completed_nodes.append(comp_node_id)
            compensated.append(comp_node_id)
            notes.append(f"executed '{comp.action_id}'")

        reason = "; ".join(notes) if notes else "no completed task required compensation"
        return executed_ok, reason


_GLOBAL_BRIDGE: DynamicWorkflowBridge | None = None


def get_dynamic_workflow_bridge() -> DynamicWorkflowBridge:
    global _GLOBAL_BRIDGE
    if _GLOBAL_BRIDGE is None:
        _GLOBAL_BRIDGE = DynamicWorkflowBridge()
    return _GLOBAL_BRIDGE
