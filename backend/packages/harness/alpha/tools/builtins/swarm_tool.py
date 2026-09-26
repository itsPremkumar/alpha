"""Model-visible control surface for the Alpha swarm v2 runtime."""

from __future__ import annotations

import json
from typing import Literal

from langchain.tools import tool

from alpha.runtime.user_context import get_effective_user_id
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.governor import get_swarm_resource_governor
from alpha.swarm.incidents import get_swarm_incident_manager
from alpha.swarm.models import SwarmBudget, SwarmMode, TaskNodeState


@tool("swarm", parse_docstring=True)
def swarm_tool(
    action: Literal[
        "evaluate",
        "spawn",
        "status",
        "step",
        "run_async",
        "expand",
        "replan",
        "claim",
        "message",
        "observe",
        "metrics",
        "leader",
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
    task_id: str = "",
    lease_id: str = "",
    topic: str = "general",
    message: str = "",
    limit: int = 50,
    max_concurrency: int = 8,
    max_tokens: int | None = None,
    max_tool_calls: int | None = None,
    max_wall_seconds: float | None = None,
    auto_replan: bool = False,
    requires_consensus: bool = False,
    idempotency_key: str = "",
    reason: str = "",
) -> str:
    """Evaluate, spawn, run, communicate with, and monitor bounded agent swarms.

    Args:
        action: Operation to perform. Supported values are evaluate, spawn,
            status, step, run_async, expand, replan, claim, message, observe,
            metrics, incidents, governor, pause, resume, and cancel.
        goal: High-level mission for evaluation or swarm creation.
        swarm_id: Existing swarm id for operations other than evaluate/spawn.
        mode: Swarm strategy: auto, parallel, map_reduce, scatter_gather,
            hierarchical, debate, ensemble, or coding_worktree.
        items_json: JSON array of independent work items.
        tasks_json: JSON array of task objects for expand/replan.
        parent_task_id: Optional parent task for an injected task.
        task_id: Task id for claim or a message association.
        lease_id: Lease returned by claim; accepted by external completion flows.
        topic: Topic used for message/observe operations.
        message: Bounded message content; message payloads are untrusted data.
        limit: Maximum rows/messages/events to return.
        max_concurrency: Maximum concurrent workers, hard-capped at 64.
        max_tokens: Optional measured token ceiling for a new swarm.
        max_tool_calls: Optional measured tool-call ceiling for a new swarm.
        max_wall_seconds: Optional wall-clock ceiling for a new swarm.
        auto_replan: Permit bounded automatic repair-task expansion.
        requires_consensus: Require explicit evidence-backed votes before
            reporting completion.
        idempotency_key: Optional owner-scoped key that makes spawn retries
            return the existing plan instead of creating a second swarm.
        reason: Optional rationale for pause/cancellation.
    """

    coordinator = get_swarm_coordinator()
    owner_id = get_effective_user_id()

    items: list[str] = []
    if items_json:
        try:
            parsed = json.loads(items_json)
            if isinstance(parsed, list):
                items = [str(item).strip() for item in parsed if str(item).strip()]
        except (TypeError, ValueError):
            return "Error: 'items_json' must be a valid JSON array."

    if action == "evaluate":
        if not goal.strip():
            return "Error: 'goal' parameter is required for evaluation."
        decision = coordinator.evaluate_intent(goal.strip(), items=items)
        return (
            "### Swarm Parallelization Feasibility Analysis\n"
            f"- **Should Swarm**: `{'YES' if decision.should_swarm else 'NO'}`\n"
            f"- **Recommended Mode**: `{decision.mode.value}`\n"
            f"- **Speedup Factor**: `{decision.estimated_speedup}x`\n"
            f"- **Estimated Serial Time**: `{decision.estimated_serial_seconds:.1f}s`\n"
            f"- **Estimated Parallel Critical Path**: `{decision.estimated_parallel_seconds:.1f}s`\n"
            f"- **Recommended Workers**: `{decision.recommended_workers}`\n"
            f"- **Rationale**: {decision.reason}\n"
        )

    if action == "spawn":
        if not goal.strip():
            return "Error: 'goal' parameter is required to spawn a swarm."
        try:
            swarm_mode = SwarmMode(mode.lower().strip())
        except ValueError:
            swarm_mode = SwarmMode.AUTO
        budget = SwarmBudget(max_tokens=max_tokens, max_tool_calls=max_tool_calls, max_wall_seconds=max_wall_seconds)
        try:
            plan = coordinator.create_swarm(
                goal=goal.strip(),
                mode=swarm_mode,
                items=items,
                max_concurrency=min(max(1, max_concurrency), 64),
                owner_id=owner_id,
                budget=budget,
                auto_replan=auto_replan,
                requires_consensus=requires_consensus,
                idempotency_key=idempotency_key.strip() or None,
            )
        except ValueError as exc:
            return f"Error: swarm could not be created: {exc}"
        tasks_summary = "\n".join(f"  - `[{task.task_id}]` {task.objective} (deps: {task.dependencies or 'none'})" for task in plan.tasks.values())
        return (
            "### Autonomous Swarm Successfully Spawned (plan created; execution is explicit)\n"
            f"- **Swarm ID**: `{plan.swarm_id}`\n"
            f"- **Goal**: {plan.goal}\n"
            f"- **Mode**: `{plan.mode.value}`\n"
            f"- **Tasks**: {len(plan.tasks)}\n"
            f"- **Task DAG**:\n{tasks_summary}\n\n"
            f"Run it with `swarm(action='run_async', swarm_id='{plan.swarm_id}')`; `step` only dispatches work."
        )

    if action in {"status", "step", "run_async", "expand", "replan", "claim", "message", "observe", "metrics", "incidents", "pause", "resume", "cancel"}:
        if not swarm_id.strip():
            return "Error: 'swarm_id' parameter is required."
        swarm_id = swarm_id.strip()
        plan = coordinator.get_swarm(swarm_id, owner_id=owner_id)
        if not plan:
            return f"Error: Swarm '{swarm_id}' not found or is not visible to this owner."

    if action == "status":
        completed = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED)
        running = sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
        failed = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.FAILED)
        cancelled = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.CANCELLED)
        pending = sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED))
        task_rows = "\n".join(f"| `{task.task_id}` | `{task.state.value}` | `{task.assigned_worker or 'unassigned'}` | `{task.lease_id or '-'}` | {task.objective[:80]} |" for task in plan.tasks.values())
        return (
            f"### Swarm Status: `{plan.swarm_id}`\n"
            f"- **Status**: `{plan.status}`\n"
            f"- **Goal**: {plan.goal}\n"
            f"- **Progress**: {completed}/{len(plan.tasks)} completed ({running} running, {pending} pending, {failed} failed, {cancelled} cancelled)\n"
            f"- **Revision**: `{plan.revision}`\n"
            f"- **Budget**: `{json.dumps(plan.budget.to_dict(), sort_keys=True)}`\n"
            f"- **Consensus**: `{json.dumps(plan.consensus, sort_keys=True) if plan.consensus else 'not evaluated'}`\n\n"
            f"| Task ID | State | Worker | Lease | Objective |\n|---|---|---|---|---|\n{task_rows}\n"
        )

    if action == "step":
        return f"Swarm scheduler tick executed (dispatch only; model work remains asynchronous): {json.dumps(coordinator.step(swarm_id), indent=2)}"

    if action == "run_async":
        if plan.status in {"completed", "failed", "cancelled", "budget_exhausted", "stalled"}:
            return f"Error: swarm is already terminal with status '{plan.status}'."
        coordinator.start_async(swarm_id)
        return f"Background runner started for swarm `{swarm_id}`."

    if action in {"expand", "replan"}:
        try:
            parsed_tasks = json.loads(tasks_json) if tasks_json else []
        except (TypeError, ValueError):
            return "Error: 'tasks_json' must be valid JSON."
        if not isinstance(parsed_tasks, list):
            return "Error: 'tasks_json' must be a JSON array of task objects."
        try:
            added = coordinator.dynamic_expand(swarm_id, parsed_tasks, parent_task_id.strip() or None)
        except ValueError as exc:
            return f"Error: invalid swarm replan: {exc}"
        return f"Swarm '{swarm_id}' dynamically expanded with {len(added)} validated tasks: {added}"

    if action == "claim":
        if not task_id.strip():
            return "Error: 'task_id' is required for claim."
        lease = coordinator.claim_task(swarm_id, task_id.strip(), owner="swarm-tool-worker")
        if lease is None:
            return f"Task '{task_id}' was not ready or could not be claimed."
        return json.dumps(lease.to_dict(), indent=2)

    if action == "message":
        try:
            published = coordinator.publish_message(
                swarm_id,
                topic=topic,
                sender="swarm-tool",
                kind="agent_message",
                content=message,
                data={"task_id": task_id} if task_id else None,
                task_id=task_id or None,
                trust="untrusted",
            )
        except ValueError as exc:
            return f"Error: message rejected: {exc}"
        return f"Message published: `{published.message_id}` at sequence `{published.sequence}`."

    if action == "observe":
        messages = coordinator.get_messages(swarm_id, topic=topic or None, task_id=task_id or None, limit=max(1, min(limit, 256)))
        return json.dumps([item.to_dict() for item in messages], indent=2, ensure_ascii=False)

    if action == "metrics":
        return json.dumps(coordinator.metrics(swarm_id), indent=2, ensure_ascii=False)

    if action == "leader":
        return json.dumps(plan.metrics.get("leader_election", {"leader": None, "reason": "not evaluated"}), indent=2, ensure_ascii=False)

    if action == "incidents":
        incidents = get_swarm_incident_manager().get_incidents(swarm_id)
        if not incidents:
            return f"No failure incidents recorded for swarm '{swarm_id}'."
        return json.dumps([incident.to_dict() for incident in incidents], indent=2, ensure_ascii=False)

    if action == "governor":
        return f"### Swarm Resource Governor Status\n```json\n{json.dumps(get_swarm_resource_governor().get_status(), indent=2)}\n```"

    if action == "pause":
        return f"Swarm '{swarm_id}' paused: {coordinator.pause_swarm(swarm_id)}"

    if action == "resume":
        return f"Swarm '{swarm_id}' resumed: {coordinator.resume_swarm(swarm_id)}"

    if action == "cancel":
        return f"Swarm '{swarm_id}' cancelled: {coordinator.cancel_swarm(swarm_id, reason=reason)}"

    return f"Unknown action '{action}'."
