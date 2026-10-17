"""Model-visible control surface for the Alpha swarm v2 runtime."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any, Literal

from langchain.tools import tool

from alpha.runtime.user_context import get_effective_user_id
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.governor import get_swarm_resource_governor
from alpha.swarm.incidents import get_swarm_incident_manager
from alpha.swarm.models import SwarmBudget, SwarmMode, TaskNodeState
from alpha.swarm.telemetry import SwarmTelemetry
from alpha.utils.assembly_io import run_assembly


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
        "strategy",
        "telemetry",
        "trace",
        "leader",
        "incidents",
        "governor",
        "compete",
        "compete_status",
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

    Also drives group-relative orchestration (GRO): `compete` runs one bounded
    candidate group against `goal` and returns the full report (winner, gates,
    verification line); `compete_status` reads a past GRO run back. Both refuse
    honestly while `gro.enabled` is false in config.yaml (`compete_status`
    stays readable — it is read-only).

    Args:
        action: Operation to perform. Supported values are evaluate, spawn,
            status, step, run_async, expand, replan, claim, message, observe,
            metrics, strategy, telemetry, trace, leader, incidents, governor,
            compete, compete_status, pause, resume, and cancel.
        goal: High-level mission for evaluation, swarm creation, or a GRO
            `compete` run.
        swarm_id: Existing swarm id for operations other than evaluate/spawn;
            optional for `governor` to include that swarm's requested and
            evidence-measured models. For `compete_status` it is the GRO run id
            (omit it to list your GRO runs, newest first). Not required by
            `compete` or a global `governor` read.
        mode: Swarm strategy: auto, parallel, map_reduce, scatter_gather,
            hierarchical, debate, ensemble, or coding_worktree.
        items_json: JSON array of independent work items; for `compete` each
            entry is an acceptance criterion every candidate must prove
            (declared criteria decide whether a winner can be `verified`).
        tasks_json: JSON array of task objects for expand/replan.
        parent_task_id: Optional parent task for an injected task.
        task_id: Task id for claim or a message association; for a `trace`
            deposit it names the leaving worker (provenance).
        lease_id: Lease returned by claim; accepted by external completion flows.
        topic: Topic used for message/observe operations; for a `trace`
            deposit it is the trace key (what the hint is about).
        message: Bounded message content; message payloads are untrusted data.
            For a `trace` deposit it is the trace payload.
        limit: Maximum rows/messages/events to return.
        max_concurrency: Maximum concurrent workers. Use 0 for auto (the system picks based on task complexity). Hard-capped at 1000.
        max_tokens: Optional measured token ceiling for a new swarm.
        max_tool_calls: Optional measured tool-call ceiling for a new swarm.
        max_wall_seconds: Optional wall-clock ceiling for a new swarm.
        auto_replan: Permit bounded automatic repair-task expansion.
        requires_consensus: Require explicit evidence-backed votes before
            reporting completion.
        idempotency_key: Optional owner-scoped key that makes spawn retries
            return the existing plan instead of creating a second swarm.
        reason: Optional rationale for pause/cancellation, or the category for
            a `trace` deposit (work, discovery, artifact, conflict, dead_end).
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
                max_concurrency=min(max(0, max_concurrency), 1000),
                owner_id=owner_id,
                budget=budget,
                auto_replan=auto_replan,
                requires_consensus=requires_consensus,
                idempotency_key=idempotency_key.strip() or None,
            )
        except ValueError as exc:
            return f"Error: swarm could not be created: {exc}"
        # create_swarm enforces the estimate against its wall budget before it
        # registers or persists the plan. Here the same pure projection is
        # included with successful plan creation so the caller can decide
        # whether to explicitly start execution.
        projection = SwarmTelemetry.cost_projection(plan)
        tasks_summary = "\n".join(f"  - `[{task.task_id}]` {task.objective} (deps: {task.dependencies or 'none'})" for task in plan.tasks.values())
        return (
            "### Autonomous Swarm Successfully Spawned (plan created; execution is explicit)\n"
            f"- **Swarm ID**: `{plan.swarm_id}`\n"
            f"- **Goal**: {plan.goal}\n"
            f"- **Mode**: `{plan.mode.value}`\n"
            f"- **Tasks**: {len(plan.tasks)}\n"
            f"- **Task DAG**:\n{tasks_summary}\n"
            f"- **Projected cost**: `{json.dumps(projection, sort_keys=True)}`\n\n"
            f"Run it with `swarm(action='run_async', swarm_id='{plan.swarm_id}')`; `step` only dispatches work."
        )

    if action == "compete":
        return _gro_compete(goal=goal, items=items, owner_id=owner_id)

    if action == "compete_status":
        return _gro_status(run_id=swarm_id.strip(), owner_id=owner_id, limit=limit)

    if action in {"status", "step", "run_async", "expand", "replan", "claim", "message", "observe", "metrics", "strategy", "telemetry", "trace", "leader", "incidents", "pause", "resume", "cancel"}:
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

    if action == "strategy":
        # The recorded choice, not a re-derivation: recomputing here could
        # disagree with what the plan actually ran under.
        strategy = dict(plan.metrics.get("strategy") or {})
        if not strategy:
            return f"### Swarm Strategy: `{plan.swarm_id}`\n- **Recorded**: `no` — no mode selection has been recorded for this plan.\n- **Mode**: `{plan.mode.value}`\n"
        # `plan_mode` is what the plan ran under; `source` is the signal that
        # chose it, which is what separates an explicit request from an
        # automatic one (the plan stores only the resolved mode).
        summary = {
            "swarm_id": plan.swarm_id,
            "plan_mode": plan.mode.value,
            **strategy,
            "candidates": plan.metrics.get("strategy_candidates") or [],
        }
        return json.dumps(summary, indent=2, ensure_ascii=False)

    if action == "telemetry":
        # A focused read of the signal set rather than the whole metrics blob,
        # so a long run can be inspected without pulling events and leases too.
        telemetry = coordinator.metrics(swarm_id)["telemetry"]
        return (
            f"### Swarm Telemetry: `{swarm_id}`\n"
            f"- **Risk signals**: {', '.join(f'`{signal}`' for signal in telemetry['risk_signals']) or 'none'}\n"
            f"- **Basis**: {telemetry['basis']}\n\n"
            f"```json\n{json.dumps(telemetry, indent=2, ensure_ascii=False)}\n```"
        )

    if action == "trace":
        if message.strip():
            try:
                trace = coordinator.deposit_trace(
                    swarm_id,
                    category=reason.strip() or "work",
                    key=topic.strip(),
                    payload=message,
                    provenance=task_id.strip() or "swarm-tool",
                    owner_id=owner_id,
                )
            except (KeyError, ValueError) as exc:
                return f"Error: trace rejected: {exc}"
            return f"Trace deposited: `{trace['trace_id']}` at strength `{trace['strength']}` (category `{trace['category']}`, key `{trace['key']}`)."
        traces = coordinator.traces(swarm_id, category=reason.strip() or None, limit=max(1, limit), owner_id=owner_id)
        if not traces:
            return f"No stigmergic traces recorded for swarm '{swarm_id}'. Traces are advisory context only and never gate a decision."
        return json.dumps(traces, indent=2, ensure_ascii=False)

    if action == "leader":
        return json.dumps(plan.metrics.get("leader_election", {"leader": None, "reason": "not evaluated"}), indent=2, ensure_ascii=False)

    if action == "incidents":
        incidents = get_swarm_incident_manager().get_incidents(swarm_id)
        if not incidents:
            return f"No failure incidents recorded for swarm '{swarm_id}'."
        return json.dumps([incident.to_dict() for incident in incidents], indent=2, ensure_ascii=False)

    if action == "governor":
        plan = None
        if swarm_id.strip():
            plan = coordinator.get_swarm(swarm_id.strip(), owner_id=owner_id)
            if plan is None:
                return f"Error: Swarm '{swarm_id.strip()}' not found or is not visible to this owner."
        return f"### Swarm Resource Governor Status\n```json\n{json.dumps(get_swarm_resource_governor().get_status(plan), indent=2)}\n```"

    if action == "pause":
        return f"Swarm '{swarm_id}' paused: {coordinator.pause_swarm(swarm_id)}"

    if action == "resume":
        return f"Swarm '{swarm_id}' resumed: {coordinator.resume_swarm(swarm_id)}"

    if action == "cancel":
        return f"Swarm '{swarm_id}' cancelled: {coordinator.cancel_swarm(swarm_id, reason=reason)}"

    return f"Unknown action '{action}'."


# --- GRO (group-relative orchestration) ---------------------------------


def _gro_assemble_tools() -> list[Any]:
    """Assemble the GRO candidate tool pool, off any running event loop.

    Assembly re-enters ``get_available_tools`` (MCP discovery, config reads)
    and ``run_assembly`` needs a running loop, so the normal path — a sync
    tool body in an executor thread — creates one here. If a loop *is* already
    running on this thread (an inline caller), the work moves to a daemon
    thread and is joined rather than nesting loops: the same fallback shape
    ``alpha.swarm.runner`` uses, so the two cannot drift apart on it.
    """

    async def _factory() -> list[Any]:
        from alpha.tools import get_available_tools

        return await run_assembly(get_available_tools, groups=None, include_mcp=True, model_name=None, subagent_enabled=False)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return list(asyncio.run(_factory()) or [])

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(_factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread below
            box["error"] = exc

    thread = threading.Thread(target=_worker, name="gro-tool-assembly", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return list(box.get("value") or [])


def _gro_compete(*, goal: str, items: list[str], owner_id: str) -> str:
    """Run one bounded GRO candidate group against ``goal`` (action=compete).

    Runs synchronously to completion — the same posture as ``task`` and
    ``arena start`` — because a sync tool body executes in an executor thread,
    never on the event loop. The candidate pool excludes the ``swarm`` tool
    itself so a candidate cannot launch a nested tournament from inside one.
    """

    if not goal.strip():
        return "Error: 'goal' parameter is required to start a GRO competition."

    from alpha.config import get_app_config

    app_config = get_app_config()
    gro_config = app_config.gro
    if not gro_config.enabled:
        return "Error: group-relative orchestration is disabled — set `gro.enabled: true` in config.yaml to use swarm(action='compete'). `compete_status` stays readable while disabled (it is read-only)."

    from alpha.gro.adapters import SubagentExecutionAdapter
    from alpha.gro.runner import run_with_gro
    from alpha.gro.store import GroStore

    try:
        assembled = _gro_assemble_tools()
    except Exception as exc:  # noqa: BLE001 - assembly failure is data for the model, not an escape
        return f"Error: GRO candidate tool pool could not be assembled: {type(exc).__name__}: {exc}"
    pool = [item for item in assembled if getattr(item, "name", None) != "swarm"]
    if not pool:
        return "Error: the candidate tool pool assembled empty; refusing to run candidates with no tools (a tool-less candidate would score worse for having fewer instruments)."

    run_id = f"gro_{uuid.uuid4().hex[:12]}"
    adapter = SubagentExecutionAdapter(thread_id="", run_id=run_id, tools=pool, app_config=app_config, user_id=owner_id or None)
    try:
        report = run_with_gro(
            goal.strip(),
            adapter=adapter,
            acceptance_criteria=items,
            config=gro_config,
            owner_id=owner_id,
            run_id=run_id,
            store=GroStore(),
        )
    except ValueError as exc:
        return f"Error: GRO run refused: {exc}"
    return report.markdown()


def _gro_status(*, run_id: str, owner_id: str, limit: int) -> str:
    """Read a GRO run back (action=compete_status); omit ``run_id`` to list.

    Read-only: it runs no candidates and changes no state. An unreadable store
    is reported as exactly that — never as "no runs".
    """

    from alpha.gro.runner import report_for_run
    from alpha.gro.store import GroStore, is_valid_run_id

    store = GroStore()
    if not run_id:
        entries = store.list_runs(owner_id=owner_id, limit=max(1, limit))
        if store.is_degraded:
            return f"Error: the GRO run store could not be read: {store.load_error}"
        if not entries:
            return "No GRO runs recorded for this owner yet. Start one with swarm(action='compete', goal='...')."
        lines = ["### GRO runs (newest first)", ""]
        for entry in entries:
            suffix = " (unreadable file)" if entry.get("unreadable") else f", selected `{entry.get('selected_candidate_id')}`"
            lines.append(f"- `{entry['run_id']}` — state `{entry.get('state', '?')}`, {entry.get('candidate_count', '?')} candidates{suffix}")
        lines.append("")
        lines.append("Read one with `swarm(action='compete_status', swarm_id='<run id>')`.")
        return "\n".join(lines)

    if not is_valid_run_id(run_id):
        return f"Error: invalid GRO run id {run_id!r}: expected 1-64 chars of [A-Za-z0-9_.-] starting alphanumeric."

    run = store.load(run_id, owner_id=owner_id)
    if run is not None:
        return report_for_run(run).markdown()
    if store.is_degraded:
        return f"Error: GRO run '{run_id}' could not be read: {store.load_error}"

    events = store.read_events(owner_id=owner_id, run_id=run_id, limit=8)
    if store.is_degraded:
        return f"Error: GRO run '{run_id}' could not be read: {store.load_error}"
    if events:
        tail = "\n".join(f"- `{event.get('type', 'unknown')}`" for event in events)
        return f"No state document for GRO run '{run_id}' exists yet (a run interrupted before completion journals events but writes its document at the end). Event journal tail:\n{tail}"
    return f"No GRO run '{run_id}' found for this owner."
