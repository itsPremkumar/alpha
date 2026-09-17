"""Built-in tool for orchestrating and monitoring autonomous agent swarms.

Allows AI agents and supervisors to evaluate parallelization feasibility,
decompose goals into dependency DAGs, spawn hybrid swarms, expand mid-flight,
monitor incidents, and coordinate autonomous deliverables.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain.tools import tool

from agent_workspace.swarm.coordinator import get_swarm_coordinator
from agent_workspace.swarm.governor import get_swarm_resource_governor
from agent_workspace.swarm.incidents import get_swarm_incident_manager
from agent_workspace.swarm.models import SwarmMode, TaskNodeState


@tool("swarm", parse_docstring=True)
def swarm_tool(
    action: Literal[
        "evaluate",
        "spawn",
        "status",
        "step",
        "expand",
        "incidents",
        "governor",
        "pause",
        "resume",
        "cancel",
    ],
    goal: str = "",
    swarm_id: str = "",
    mode: str = "auto",
    items_json: str = "[]",
    tasks_json: str = "[]",
    parent_task_id: str = "",
    max_concurrency: int = 8,
    reason: str = "",
) -> str:
    """Evaluate, spawn, monitor, and coordinate autonomous agent swarms.

    Args:
        action: Operation to perform:
            - 'evaluate': Check whether a goal warrants a swarm, returning mathematical speedup and critical path.
            - 'spawn': Decompose a goal and initialize an autonomous swarm plan.
            - 'status': Check the progress, active workers, and critical path of an active swarm.
            - 'step': Advance execution: dispatch ready tasks, check stragglers, and trigger aggregation if finished.
            - 'expand': Dynamically inject new tasks into an active swarm DAG mid-flight.
            - 'incidents': Inspect failure incidents and automated succession recovery logs.
            - 'governor': Check model routing tiers and rate-limit adaptive throttling status.
            - 'pause': Pause execution of a running swarm.
            - 'resume': Resume a paused swarm.
            - 'cancel': Abort an ongoing swarm run.
        goal: The high-level mission or goal for the swarm.
        swarm_id: The ID of an existing swarm (required for status, step, expand, incidents, pause, resume, cancel).
        mode: Swarm strategy: 'auto', 'parallel', 'map_reduce', 'scatter_gather', 'debate', 'ensemble', 'coding_worktree'.
        items_json: JSON array of string items to process in parallel (for batch map-reduce workloads).
        tasks_json: JSON array of task objects to dynamically inject with 'expand' action.
        parent_task_id: Optional parent task ID for dynamically injected tasks.
        max_concurrency: Maximum number of concurrent workers (default 8, max 12).
        reason: Optional rationale for pause, cancellation, or override.
    """
    coordinator = get_swarm_coordinator()

    # Parse items
    items: list[str] = []
    if items_json:
        try:
            parsed = json.loads(items_json)
            if isinstance(parsed, list):
                items = [str(x) for x in parsed]
        except Exception:
            items = []

    # 1. EVALUATE
    if action == "evaluate":
        if not goal.strip():
            return "Error: 'goal' parameter is required for evaluation."
        decision = coordinator.evaluate_intent(goal.strip(), items=items)
        return (
            f"### Swarm Parallelization Feasibility Analysis\n"
            f"- **Should Swarm**: `{'YES' if decision.should_swarm else 'NO'}`\n"
            f"- **Recommended Mode**: `{decision.mode.value}`\n"
            f"- **Speedup Factor**: `{decision.estimated_speedup}x`\n"
            f"- **Estimated Serial Time**: `{decision.estimated_serial_seconds:.1f}s`\n"
            f"- **Estimated Parallel Critical Path**: `{decision.estimated_parallel_seconds:.1f}s`\n"
            f"- **Recommended Workers**: `{decision.recommended_workers}`\n"
            f"- **Rationale**: {decision.reason}\n"
        )

    # 2. SPAWN
    if action == "spawn":
        if not goal.strip():
            return "Error: 'goal' parameter is required to spawn a swarm."
        try:
            swarm_mode = SwarmMode(mode.lower().strip())
        except ValueError:
            swarm_mode = SwarmMode.AUTO

        plan = coordinator.create_swarm(
            goal=goal.strip(),
            mode=swarm_mode,
            items=items,
            max_concurrency=min(max(1, max_concurrency), 12),
        )

        tasks_summary = "\n".join(f"  - `[{t.task_id}]` {t.objective} (deps: {t.dependencies or 'none'})" for t in plan.tasks.values())

        return (
            f"### Autonomous Swarm Successfully Spawned!\n"
            f"- **Swarm ID**: `{plan.swarm_id}`\n"
            f"- **Goal**: {plan.goal}\n"
            f"- **Mode**: `{plan.mode.value}`\n"
            f"- **Speedup Factor**: `{plan.estimated_speedup}x`\n"
            f"- **Critical Path Duration**: `{plan.critical_path_seconds:.1f}s`\n"
            f"- **Total Tasks**: {len(plan.tasks)}\n"
            f"- **Task DAG**:\n{tasks_summary}\n\n"
            f"Use `swarm(action='step', swarm_id='{plan.swarm_id}')` to drive task execution."
        )

    # 3. STATUS
    if action == "status":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        plan = coordinator.get_swarm(swarm_id.strip())
        if not plan:
            return f"Error: Swarm '{swarm_id}' not found."

        completed = sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.COMPLETED)
        running = sum(1 for t in plan.tasks.values() if t.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
        failed = sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.FAILED)
        pending = sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.PENDING)

        tasks_table = "\n".join(f"| `{t.task_id}` | `{t.state.value}` | `{t.assigned_worker or 'unassigned'}` | {t.objective[:50]}... |" for t in plan.tasks.values())

        return (
            f"### Swarm Status: `{plan.swarm_id}`\n"
            f"- **Status**: `{plan.status}`\n"
            f"- **Goal**: {plan.goal}\n"
            f"- **Progress**: {completed}/{len(plan.tasks)} completed ({running} running, {pending} pending, {failed} failed)\n"
            f"- **Speedup**: `{plan.estimated_speedup}x` (Critical Path: `{plan.critical_path_seconds:.1f}s`)\n\n"
            f"| Task ID | State | Worker | Objective |\n"
            f"|---|---|---|---|\n"
            f"{tasks_table}\n"
        )

    # 4. STEP
    if action == "step":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        res = coordinator.step(swarm_id.strip())
        return f"Swarm step executed. Result: {json.dumps(res, indent=2)}"

    # 5. EXPAND (Mid-flight replanning)
    if action == "expand":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        try:
            parsed_tasks = json.loads(tasks_json) if tasks_json else []
            if not isinstance(parsed_tasks, list):
                parsed_tasks = [parsed_tasks]
        except Exception:
            return "Error: 'tasks_json' must be a valid JSON array of task objects."

        added = coordinator.dynamic_expand(
            swarm_id=swarm_id.strip(),
            new_tasks=parsed_tasks,
            parent_task_id=parent_task_id.strip() or None,
        )
        return f"Swarm '{swarm_id}' dynamically expanded with {len(added)} new tasks: {added}"

    # 6. INCIDENTS
    if action == "incidents":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        inc_mgr = get_swarm_incident_manager()
        incidents = inc_mgr.get_incidents(swarm_id.strip())
        if not incidents:
            return f"No failure incidents recorded for swarm '{swarm_id}'."
        lines = [f"### Swarm Incidents ({len(incidents)})"]
        for inc in incidents:
            status = "RESOLVED via @" + str(inc.assigned_successor) if inc.resolved else "UNRESOLVED"
            lines.append(f"- `[{inc.incident_id}]` Task `{inc.task_id}` failed by @{inc.failed_worker}: {inc.error_message} ({status})")
        return "\n".join(lines)

    # 7. GOVERNOR
    if action == "governor":
        gov = get_swarm_resource_governor()
        status = gov.get_status()
        return f"### Swarm Resource Governor Status\n```json\n{json.dumps(status, indent=2)}\n```"

    # 8. PAUSE
    if action == "pause":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        ok = coordinator.pause_swarm(swarm_id.strip())
        return f"Swarm '{swarm_id}' paused: {ok}"

    # 9. RESUME
    if action == "resume":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        ok = coordinator.resume_swarm(swarm_id.strip())
        return f"Swarm '{swarm_id}' resumed: {ok}"

    # 10. CANCEL
    if action == "cancel":
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        ok = coordinator.cancel_swarm(swarm_id.strip(), reason=reason)
        return f"Swarm '{swarm_id}' cancelled: {ok}"

    return f"Unknown action '{action}'."
