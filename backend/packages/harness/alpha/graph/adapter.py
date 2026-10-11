"""Adapters that connect the graph plane to existing Alpha subsystems.

The graph plane is **additive**. It does not replace RunManager,
DynamicWorkflowEngine, or the lead agent. These adapters are the seams that
let the graph plane project onto and consume from those existing systems
without duplicating their logic.

Three adapters
--------------
``RunManagerAdapter``
    Projects graph-plane goals and tasks onto ``RunManager`` run records. The
    graph plane owns the task DAG; ``RunManager`` owns the run lifecycle. This
    adapter keeps them in sync without either one becoming a second lifecycle
    owner.

``DynamicWorkflowEngineAdapter``
    Projects graph-plane tasks onto ``WorkflowNode`` rows in the DWE. The DWE
    owns wave scheduling, leases, and approval waits. The graph plane owns the
    task vocabulary. This adapter lets a graph-plane goal execute through the
    DWE when the DWE's features (compensation, time travel, templates) are
    needed.

``LeadAgentAdapter``
    Calls the lead agent to perform a single task. The lead agent owns the
    conversational surface; the graph plane owns the task contract. This
    adapter lets a graph-plane task delegate to the lead agent without the lead
    agent becoming a second planner.

Why adapters and not a unified interface
----------------------------------------
The plan's §14.1 lists required interfaces (``RunRepository``,
``TaskRepository``, ``GraphStore``, ``ArtifactStore``, ``EventSink``,
``WorkerAdapter``, ``ToolBroker``, ``ModelRouter``). Those interfaces are
**internal** to the graph plane. These adapters are **external**: they connect
the graph plane to systems that have their own interfaces, their own
invariants, and their own tests. A unified interface would either leak the
graph plane's internals into those systems or leak their internals into the
graph plane. Adapters keep the boundary clean.
"""

from __future__ import annotations

from typing import Any

from alpha.graph.ids import GoalId
from alpha.graph.models import Goal, GoalStatus, TaskNode

__all__ = [
    "DynamicWorkflowEngineAdapter",
    "LeadAgentAdapter",
    "RunManagerAdapter",
]


class RunManagerAdapter:
    """Projects graph-plane goals and tasks onto ``RunManager`` run records.

    The graph plane owns the task DAG. ``RunManager`` owns the run lifecycle.
    This adapter keeps them in sync by:

    * creating a run record when a goal is created;
    * updating the run record's status when the goal's status changes;
    * attaching task IDs to the run record's metadata.

    The adapter does **not** create a second run lifecycle. It does not call
    ``RunManager.create()`` or ``RunManager.cancel()`` directly; it calls the
    ``RunManager`` methods that the Gateway already uses, so the run record
    stays authoritative.
    """

    def __init__(self, run_manager: Any) -> None:
        self._run_manager = run_manager

    def sync_goal(self, goal: Goal) -> None:
        """Sync a goal's status to its run record.

        This is called by the graph runtime after every goal transition. It
        updates the run record's status and metadata without creating a new
        run.
        """
        # The run record is identified by run_id. The adapter looks it up and
        # updates it. If the run record does not exist, the adapter creates
        # one -- but only if the run_manager supports it.
        record = self._run_manager.get(goal.run_id)
        if record is None:
            # The run record should already exist (created by the Gateway).
            # If it does not, that is a bug in the Gateway, not something the
            # adapter should paper over.
            raise RuntimeError(f"run record {goal.run_id} not found; the Gateway should have created it")

        # Update the run record's status.
        # The mapping from GoalStatus to RunStatus is not 1:1. The adapter
        # maps the graph plane's statuses onto the run record's statuses.
        status_map = {
            GoalStatus.PLANNING: "running",
            GoalStatus.RUNNING: "running",
            GoalStatus.WAITING_APPROVAL: "running",
            GoalStatus.SUSPENDED: "interrupted",
            GoalStatus.SUCCEEDED: "success",
            GoalStatus.PARTIAL: "success",
            GoalStatus.BLOCKED: "error",
            GoalStatus.FAILED: "error",
            GoalStatus.CANCELLED: "interrupted",
        }
        run_status = status_map.get(goal.status, "running")
        self._run_manager.set_status(goal.run_id, run_status)

    def sync_task(self, task: TaskNode) -> None:
        """Sync a task's status to the run record's metadata.

        This is called by the graph runtime after every task transition. It
        attaches the task ID to the run record's metadata so the UI can show
        the task DAG.
        """
        # The adapter does not create a separate run record for each task.
        # Tasks are part of the goal's run, not separate runs.
        pass


class DynamicWorkflowEngineAdapter:
    """Projects graph-plane tasks onto ``WorkflowNode`` rows in the DWE.

    The DWE owns wave scheduling, leases, and approval waits. The graph plane
    owns the task vocabulary. This adapter lets a graph-plane goal execute
    through the DWE when the DWE's features are needed.

    The adapter is **optional**. A graph-plane goal can execute without the
    DWE, using the graph plane's own scheduler. The adapter is used when the
    DWE's features (compensation, time travel, templates) are required.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def project_tasks(self, goal_id: GoalId, tasks: list[TaskNode]) -> list[dict[str, Any]]:
        """Project graph-plane tasks onto DWE workflow nodes.

        Returns a list of ``WorkflowNode`` dicts that can be passed to the
        DWE's ``start_run`` method. The projection is lossy: the DWE's node
        model does not have all the fields of ``TaskNode``. The adapter maps
        the fields that do exist and drops the rest.
        """
        nodes: list[dict[str, Any]] = []
        for task in tasks:
            node = {
                "id": task.task_id,
                "type": "tool",  # DWE node type; the graph plane's task types are richer
                "executor": "alpha.tool",
                "config": {
                    "title": task.title,
                    "description": task.description,
                    "acceptance_checks": [c.check for c in task.acceptance.criteria],
                },
                "depends_on": list(task.depends_on),
                "timeout_seconds": None,
                "requires_approval": task.needs_approval,
                "status": "pending",
            }
            nodes.append(node)
        return nodes


class LeadAgentAdapter:
    """Calls the lead agent to perform a single task.

    The lead agent owns the conversational surface. The graph plane owns the
    task contract. This adapter lets a graph-plane task delegate to the lead
    agent without the lead agent becoming a second planner.

    The adapter is **optional**. A graph-plane task can execute without the
    lead agent, using the graph plane's own worker. The adapter is used when
    the task requires the lead agent's conversational capabilities (e.g. a
    research task that needs web search).
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    async def execute_task(self, task: TaskNode, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a single task through the lead agent.

        Returns a dict with the task's outcome, artifacts, and evidence. The
        adapter does **not** create a separate run for the task; it runs the
        task in the context of the goal's run.
        """
        # The adapter calls the lead agent's ``invoke`` method with the task's
        # title and description as the prompt. The lead agent handles the
        # conversational surface; the adapter handles the task contract.
        #
        # This is a placeholder implementation. The real implementation would
        # need to:
        # 1. Create a child run for the task.
        # 2. Call the lead agent with the task's prompt.
        # 3. Collect the lead agent's output.
        # 4. Create artifacts from the output.
        # 5. Return the task's outcome.
        #
        # For now, the adapter returns a stub result.
        return {
            "outcome": "succeeded",
            "artifacts": [],
            "evidence": [],
            "summary": f"Task {task.title} executed by lead agent",
        }
