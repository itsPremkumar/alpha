"""Adapters that connect the graph plane to existing Alpha subsystems.

The graph plane is **additive**. It does not replace RunManager,
DynamicWorkflowEngine, or the lead agent. These adapters are the seams that
let the graph plane project onto and consume from those existing systems
without duplicating their logic.

Four adapters
-------------
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

``SpecialistTeamAdapter``
    Binds the graph plane onto Alpha's existing multi-agent coordination -- the
    swarm runtime's specialist team composition -- instead of building a second
    agent registry. See its docstring for why this is an adapter and not a
    reimplementation.

Why adapters and not a unified interface
----------------------------------------
The plan's section 14.1 lists required interfaces (``RunRepository``,
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

from alpha.capabilities.eligibility import infer_capability_tags, normalize_tags
from alpha.graph.ids import GoalId, make_id
from alpha.graph.models import Goal, GoalStatus, TaskNode
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from alpha.swarm.team import (
    TeamAssignment,
    assign_specialists,
    resolve_roster,
)

__all__ = [
    "DynamicWorkflowEngineAdapter",
    "LeadAgentAdapter",
    "RunManagerAdapter",
    "SpecialistTeamAdapter",
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

        Called after every task transition so the UI can show the task DAG
        without reading the graph plane's ledger.

        This is a **best-effort projection**: a run manager that cannot carry
        task metadata is logged and the run continues, because the ledger is
        authoritative (``graph/AGENTS.md`` invariant 7). It is deliberately not
        a silent ``pass`` -- an empty method reads as "wired" in review and
        ships a UI that never learns about tasks.
        """
        try:
            reporter = getattr(self._run_manager, "add_task_metadata", None) or getattr(self._run_manager, "update_metadata", None)
            if reporter is None:
                return
            reporter(task.task_id, {"task_id": task.task_id, "status": task.status.value, "title": task.title})
        except Exception:
            import logging

            logging.getLogger(__name__).debug("RunManagerAdapter.sync_task: run manager cannot carry task metadata for %s", task.task_id, exc_info=True)


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

        Honesty over completeness
        -------------------------
        An earlier version returned ``{"outcome": "succeeded", ...}`` for a task
        it never ran. That single line broke three load-bearing rules at once:
        ``graph/AGENTS.md`` invariant 1 ("a task cannot succeed unverified"),
        plan section 19 ("worker says done but no artifact exists ->
        verification fails"), and plan B L327 ("never let the same worker's
        self-report substitute for independent verification"). A stub that
        reports success is the most dangerous shape in this codebase, because it
        converts "not built yet" into "done" for every downstream reader.

        Until a real worker is bound (the plan's Phase 5), the adapter reports
        ``blocked`` with the reason, so ``verify_task`` fails loudly and the
        goal ends ``partial`` -- which is what is actually true.
        """
        return {
            "outcome": "blocked",
            "artifacts": [],
            "evidence": [],
            "reason": "no lead-agent worker is bound to the graph plane; bind one through GraphRuntime before dispatching (plan Phase 5)",
            "summary": f"Task {task.title} was not executed: no worker bound.",
        }


class SpecialistTeamAdapter:
    """Binds the graph plane onto Alpha's existing multi-agent coordination.

    Why an adapter and not an agent registry
    ----------------------------------------
    Doc B (the multi-agent plan) specifies ``AgentProfile`` + ``AgentRegistry``
    + capability-first eligibility + *"a capability gap instead of silently
    substituting a general agent"*. Building that inside the graph plane would
    make it a **second** agent-coordination system in one repository.

    Alpha already has one, and ``TEAM_RUNTIME.md`` states the rule it follows:

        "A team is not a new lifecycle, a new plan type, or a new run record.
         It is an ordinary SwarmPlan whose task nodes carry a declared
         capability requirement and an assignment to a named specialist."

    So this adapter **projects onto** it rather than reimplementing it:

    * :func:`alpha.swarm.team.assign_specialists` is the capability filter --
      a hard filter through :mod:`alpha.capabilities.eligibility`, with every
      rejected candidate and the reason recorded.
    * :mod:`alpha.capabilities.eligibility` is the *shared* capability
      vocabulary, so a graph-plane task and a swarm task mean the same thing by
      "needs shell access".
    * The real differentiated worker is ``SpecialistSubagentWorker`` over the
      ``SubagentExecutor`` -- the thing that makes a specialist a specialist
      rather than a renamed generalist.

    What this deliberately does **not** do
    --------------------------------------
    * It does **not** persist a ``SwarmPlan``, start a swarm, or take a lease.
      This answers the question *"who should take this work, and who cannot?"*
      -- it is not a second scheduler competing with ``SwarmScheduler`` or the
      DWE for the same run.
    * It never writes ``state``, ``acceptance_status`` or ``verification``.
    * It never substitutes a generalist for a missing capability. An
      unassignable task is reported as a capability gap, named, with the full
      rejection list -- the same contract ``assign_specialists`` already gives.
    """

    def __init__(self, roster: Any = None) -> None:
        #: An explicit roster (``list[SpecialistProfile]`` or raw config
        #: entries). ``None`` resolves through the registered provider, which
        #: is what the ``specialists:`` config section feeds.
        self._roster = roster

    def describe_requirement(self, task: TaskNode) -> tuple[str, list[str]]:
        """What capability does this task require, and how firm is the answer?

        Returns ``(source, tags)`` where ``source`` is ``declared`` or
        ``inferred`` -- the distinction :mod:`alpha.swarm.team` enforces
        differently and that this adapter must not flatten.

        A **declared** requirement is the graph-plane task's own
        ``required_capabilities``: strict, every tag required. An **inferred**
        one is read from the task's directive by the shared keyword vocabulary
        and is only a hint. A task with neither is ``none`` -- unconstrained,
        which is not the same as "matches everything".
        """
        declared = [capability.capability for capability in task.required_capabilities if capability.required]
        if declared:
            return "declared", sorted(normalize_tags(declared))
        inferred = infer_capability_tags(task.description or task.title, limit=4)
        if inferred:
            return "inferred", sorted(inferred)
        return "none", []

    def build_plan(self, goal: Goal, tasks: list[TaskNode]) -> SwarmPlan:
        """Project a goal and its tasks into a ``SwarmPlan`` for assignment.

        This is a **projection**, not a run: the returned plan is never
        persisted, never scheduled, and never leased. It exists so the existing
        capability filter can answer the question the graph plane needs
        answered before it dispatches anything.
        """
        swarm_tasks: dict[str, SwarmTaskNode] = {}
        for task in tasks:
            source, tags = self.describe_requirement(task)
            swarm_tasks[task.task_id] = SwarmTaskNode(
                task_id=task.task_id,
                objective=task.description or task.title,
                dependencies=list(task.depends_on),
                capability_tags=tags,
                capability_source=source,
                max_attempts=task.max_attempts,
                priority=task.priority,
            )
        return SwarmPlan(
            swarm_id=make_id("swarm"),
            goal=goal.normalized_objective or goal.user_request,
            mode=SwarmMode.AUTO,
            tasks=swarm_tasks,
            max_concurrency=goal.budget.max_concurrent_tasks or 2,
        )

    def assign(self, plan: SwarmPlan) -> TeamAssignment:
        """Run the existing specialist assignment over *plan*."""
        return assign_specialists(plan, self._roster)

    def apply_assignments(self, ledger: Any, goal_id: GoalId, assignment: TeamAssignment) -> list[TaskNode]:
        """Write the chosen ``assigned_worker`` back onto the graph-plane tasks.

        The ledger stays authoritative (``graph/AGENTS.md`` invariant 7): this
        copies the swarm composition's *decision* into the graph plane's own
        records so a UI reading the ledger sees who is doing the work, without
        the swarm ever becoming a second source of truth for task state.
        """
        by_task = {decision.get("task_id"): decision for decision in assignment.decisions if decision.get("task_id")}
        updated: list[TaskNode] = []
        for task in ledger.list_tasks(goal_id=goal_id):
            decision = by_task.get(task.task_id)
            if decision is None:
                continue
            # ``specialist`` is the key ``assign_specialists`` records the chosen
            # profile under; ``worker`` does not exist on that payload.
            task.assigned_worker = str(decision.get("specialist") or "")
            updated.append(ledger.update_task(task))
        return updated

    def capability_gaps(self, assignment: TeamAssignment) -> list[dict[str, Any]]:
        """The tasks nobody can take, each with the missing tags and the reason.

        A caller that ignores this list can still run the work on the default
        worker path -- but then it is choosing to, with the gap in front of it,
        rather than reading a plan that quietly implies coverage.
        """
        return [dict(item) for item in assignment.unassigned]

    def roster_status(self) -> dict[str, Any]:
        """Whether a specialist roster is registered at all.

        ``TEAM_RUNTIME.md`` is explicit that no provider registered means every
        task keeps its default worker and the report says ``no_roster_registered``
        -- disclosed, never presented as a team.
        """
        roster, provenance = resolve_roster()
        status = dict(provenance)
        status["specialists"] = [profile.name for profile in roster]
        return status
