"""Durable, bounded asynchronous execution for Alpha swarm DAGs.

The runner owns only orchestration.  It dispatches work, renews task leases,
records measured usage, fences stale results, and aggregates terminal state.
Provider calls remain outside the asyncio event loop, but every synchronous
result is accepted only when its lease still belongs to that attempt.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from typing import Any

from alpha.agents.middlewares.receipt_verification import validate_receipt_verdict
from alpha.config.runtime_paths import project_root, runtime_home
from alpha.sandbox.worktree_strategy import (
    Assurance,
    WorktreeMode,
    is_protected_branch,
    select_strategy,
)
from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.governor import get_swarm_resource_governor
from alpha.swarm.grounding import ground_claims
from alpha.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState, is_terminal_swarm_status
from alpha.swarm.reflection import SwarmReflector
from alpha.swarm.scheduler import SwarmPlanValidationError, SwarmScheduler
from alpha.swarm.team import SPECIALIST_WORKER_TYPE, resolve_roster
from alpha.swarm.watchdog import SwarmWatchdog
from alpha.swarm.worker import (
    CodingWorktreeWorker,
    EphemeralSubagentWorker,
    SpecialistBotWorker,
    SpecialistSubagentWorker,
)

logger = logging.getLogger(__name__)

# Module-level registry preserves the existing single-process API contract.
# It is an optimization, not the source of truth: persisted plans and leases
# remain authoritative after a process restart.
_ACTIVE_SWARM_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_SWARM_THREADS: dict[str, threading.Thread] = {}
_ACTIVE_SWARM_REGISTRY_LOCK = threading.RLock()


def _worktree_branch_name(task_id: str) -> str:
    """Return a bounded, collision-resistant branch for the original task id."""
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]", "-", task_id).strip("-.") or "task"
    while ".." in safe_task_id:
        safe_task_id = safe_task_id.replace("..", "-")
    # Normalization is intentionally lossy (for example, ``task:a`` and
    # ``task-a``); retain a digest of the unmodified identifier so parallel
    # coding tasks can never silently select the same worktree branch.
    suffix = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    return f"wt-{safe_task_id[:180]}-{suffix}"


def _final_event_for(status: str) -> str:
    if status == "completed":
        return "SWARM_COMPLETED"
    if status == "failed":
        return "SWARM_FAILED"
    if status == "budget_exhausted":
        return "SWARM_BUDGET_EXHAUSTED"
    if status == "stalled":
        return "SWARM_STALLED"
    return "SWARM_PARTIAL_SUCCESS"


def shutdown_background_swarms(timeout: float = 5.0) -> dict[str, Any]:
    """Stop every background swarm owned by this process.

    Gateway-shutdown entry point.  A fresh runner is used because shutdown only
    touches the module-level registries and the coordinator; it needs no
    worker state of its own.
    """
    from alpha.swarm.coordinator import get_swarm_coordinator

    return AsyncSwarmRunner(get_swarm_coordinator()).shutdown(timeout=timeout)


class AsyncSwarmRunner:
    """Asynchronous background execution engine for :class:`SwarmPlan`."""

    def __init__(
        self,
        coordinator: Any,
        poll_interval: float = 0.2,
        *,
        lease_seconds: float = 60.0,
        reflector: SwarmReflector | None = None,
        provider: str = "default",
    ):
        self.coordinator = coordinator
        self.poll_interval = max(0.01, float(poll_interval))
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.reflector = reflector or SwarmReflector()
        self.provider = str(provider or "default")
        self._active_tasks: dict[str, asyncio.Task | threading.Thread] = {}
        # Set by ``shutdown()``.  A background loop checks it before leasing new
        # work so a stopping process does not start a task it cannot finish.
        self._shutting_down = False
        # One shared tool pool for every specialist in a run, assembled at most
        # once and threaded through each worker. Per-worker assembly would
        # re-enter MCP discovery once per task; the pool is the same list
        # object handed to each ``SubagentExecutor``, which filters it down to
        # that specialist's allowlist, so sharing is safe.
        self._specialist_tool_pool: list[Any] | None = None
        self._specialist_tool_pool_error: str | None = None
        self.thread_id: str | None = None

    def is_running(self, swarm_id: str) -> bool:
        with _ACTIVE_SWARM_REGISTRY_LOCK:
            thread = _ACTIVE_SWARM_THREADS.get(swarm_id)
            if thread is not None and thread.is_alive():
                return True
            task = _ACTIVE_SWARM_TASKS.get(swarm_id) or self._active_tasks.get(swarm_id)
            return task is not None and not task.done()

    def _build_worker(self, task_node: SwarmTaskNode):
        if task_node.worker_type == SPECIALIST_WORKER_TYPE and task_node.assigned_worker:
            # A declared specialist: a real tool-using subagent with its own
            # system prompt and tool allowlist, not a renamed generalist.
            # Resolved lazily and by name so an unknown specialist is an
            # honest, named failure rather than a silent downgrade to a
            # generic worker.
            roster, provenance = resolve_roster()
            profile = next((item for item in roster if item.name == task_node.assigned_worker), None)
            if profile is None:
                source = provenance.get("source") or "no registered specialist roster"
                raise RuntimeError(f"assigned specialist {task_node.assigned_worker!r} is unavailable in the current roster ({source}); refusing to run it as a generalist")
            if self._specialist_tool_pool is not None:
                available_tools = {str(getattr(tool, "name", "")) for tool in self._specialist_tool_pool}
                missing_tools = sorted(set(profile.tools or []) - available_tools)
                if missing_tools:
                    raise RuntimeError(f"assigned specialist {profile.name!r} declares configured tools that are unavailable in the assembled pool: {', '.join(missing_tools)}")
            if task_node.capability_tags:
                # G7: a task whose capability tags are all read-only gets a
                # worker structurally unable to write — the same measured
                # signals the worktree branch routes isolation from, and the
                # same single derivation (`task_signals_for`), never a second
                # opinion that could disagree with the isolation decision.
                from alpha.sandbox.worktree_strategy import task_signals_for

                read_only = task_signals_for(task_node).read_only
            else:
                read_only = False
            return SpecialistSubagentWorker(
                task_node.assigned_worker,
                agent_type=profile.agent_type or (task_node.result_payload or {}).get("specialist_agent_type") or None,
                tools=profile.tools,
                skills=profile.skills or None,
                system_prompt=profile.system_prompt,
                model=task_node.model_override,
                tool_pool=self._specialist_tool_pool,
                tool_pool_error=self._specialist_tool_pool_error,
                thread_id=self.thread_id,
                run_id=None,
                read_only=read_only,
            )
        if task_node.worker_type == "permanent_bot" and task_node.assigned_worker:
            return SpecialistBotWorker(task_node.assigned_worker)
        if task_node.worktree_path:
            # The path is descriptive task metadata; WorktreeManager always
            # receives the server's current project root, never a
            # model-supplied arbitrary host directory.
            branch_name = _worktree_branch_name(task_node.task_id)
            if is_protected_branch(branch_name):
                raise RuntimeError(f"refusing to run task {task_node.task_id!r} on protected branch {branch_name!r}")
            # Derive the signals from the task's own measured shape rather than
            # asserting that every coding task produces a mergeable diff. A
            # task that only carries read-only capability tags (research,
            # review, audit) is now treated as read-only and gets the read
            # isolation tier; one that carries write tags is treated as a
            # writer; one that carries no tags at all is unknown and resolves to
            # the recoverable default. The policy engine already exists and
            # already handles each of these cases — the bug was that the swarm
            # never fed it anything but True.
            from alpha.sandbox.worktree_strategy import task_signals_for

            selection = select_strategy(task_signals_for(task_node))
            if selection.assurance is not Assurance.STANDARD or selection.mode is not WorktreeMode.WORKTREE:
                raise RuntimeError(f"worktree isolation is unavailable for task {task_node.task_id!r} (selected {selection.mode}, assurance {selection.assurance}): {selection.reason}")
            return CodingWorktreeWorker(
                repo_root=project_root(),
                branch_name=branch_name,
                base_worktree_dir=runtime_home() / "swarm" / "worktrees",
                # A worktree that may merge is one whose change must exist as a
                # commit on a branch, and the merge must be judged before the
                # task is called done.
                auto_commit=selection.mergeable,
                simulate_merge=selection.mergeable,
            )
        return EphemeralSubagentWorker(task_node.task_id, model=task_node.model_override)

    async def _prepare_specialist_pool(self, plan: SwarmPlan) -> None:
        """Assemble the shared specialist tool pool once, disclosing failure.

        Assembly runs on the dedicated assembly pool rather than
        ``asyncio.to_thread`` because it re-enters tool assembly, which can
        block for the full MCP discovery duration; parking the default executor
        would stall every other offload in the process.
        """

        try:
            from alpha.config import get_app_config
            from alpha.subagents.builtins import BUILTIN_SUBAGENTS
            from alpha.subagents.config import resolve_subagent_model_name
            from alpha.tools import get_available_tools
            from alpha.utils.assembly_io import run_assembly

            app_config = get_app_config()
            if not getattr(app_config, "models", None):
                raise RuntimeError("No chat models are configured; a declared specialist cannot execute its task.")
            probe = BUILTIN_SUBAGENTS.get("general-purpose")
            probe_config = probe if probe is not None else None
            model_name = resolve_subagent_model_name(probe_config, None, app_config=app_config) if probe_config is not None else None
            self._specialist_tool_pool = await run_assembly(
                get_available_tools,
                groups=None,
                model_name=model_name,
                subagent_enabled=False,
                include_upload_tool=False,
                app_config=app_config,
            )
        except Exception as exc:
            # Fail loudly into the plan, then fail closed per task. The
            # specialist raises with this reason, the runner records an honest
            # task failure, and the report lists it as unknown.
            logger.exception("Failed to assemble the swarm specialist tool pool")
            self._specialist_tool_pool = None
            self._specialist_tool_pool_error = f"specialist tool pool unavailable: {type(exc).__name__}: {exc}"
            plan.metrics["specialist_tool_pool"] = {"assembled": False, "error": self._specialist_tool_pool_error}
            return
        plan.metrics["specialist_tool_pool"] = {"assembled": True, "tool_count": len(self._specialist_tool_pool or [])}

    def _stop_active_tasks(self, active: set[asyncio.Task]) -> None:
        for task in list(active):
            if not task.done():
                task.cancel()

    async def _finalize_budget(self, plan: SwarmPlan, active: set[asyncio.Task]) -> dict[str, Any]:
        """Stop dispatch and fence in-flight results when a hard budget is hit."""

        with self.coordinator.state_lock:
            reason = plan.budget.exhausted_reason or "budget_exhausted"
            plan.status = "budget_exhausted"
            plan.terminal_reason = reason
            plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for task in plan.tasks.values():
                if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED, TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                    task.state = TaskNodeState.CANCELLED
                    task.error_message = f"budget_exhausted: {reason}"
                    task.completed_at = time.time()
                    task.lease_id = None
                    task.lease_owner = None
                    task.lease_expires_at = None
            plan.final_result = f"# Swarm stopped: budget exhausted\n\n- **Reason**: `{reason}`\n- **Measured tokens**: {plan.budget.used_tokens}\n- **Measured tool calls**: {plan.budget.used_tool_calls}"
            self.coordinator.append_event(
                plan.swarm_id,
                "SWARM_BUDGET_EXHAUSTED",
                details=plan.budget.to_dict(),
            )
            self.coordinator.checkpoint(plan.swarm_id)
        self._stop_active_tasks(active)
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        return {"status": plan.status, "budget": plan.budget.to_dict(), "aggregated": False}

    async def run_swarm_async(self, swarm_id: str) -> dict[str, Any]:
        """Run the swarm DAG to a truthful terminal or explicitly paused state."""

        plan: SwarmPlan | None = self.coordinator.get_swarm(swarm_id)
        if not plan:
            return {"status": "not_found", "error": f"Swarm '{swarm_id}' not found."}

        completed_count = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED)
        if is_terminal_swarm_status(plan.status):
            return {"status": plan.status, "tasks_completed": completed_count, "terminal_reason": plan.terminal_reason}

        try:
            SwarmScheduler(plan).validate_graph()
        except SwarmPlanValidationError as exc:
            with self.coordinator.state_lock:
                plan.status = "failed"
                plan.terminal_reason = f"invalid_swarm_plan: {exc}"
                plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                plan.final_result = f"Swarm plan validation failed: {exc}"
                self.coordinator.append_event(
                    swarm_id,
                    "SWARM_PLAN_INVALID",
                    details={"error": str(exc)},
                )
                self.coordinator.checkpoint(swarm_id)
            return {"status": plan.status, "tasks_completed": completed_count, "error": str(exc)}

        # Assemble the specialist tool pool ONCE, before any dispatch, and only
        # when the plan actually has specialists. A plan with no declared
        # specialist must not pay MCP discovery, and a pool that fails to
        # assemble is recorded as a reason the specialists will fail -- not
        # swallowed, and not a silent downgrade to a generic worker.
        if any(task.worker_type == SPECIALIST_WORKER_TYPE and task.assigned_worker for task in plan.tasks.values()):
            await self._prepare_specialist_pool(plan)

        plan.status = "running"
        plan.terminal_reason = None
        plan.budget.start()
        plan.metrics.setdefault("started_at", time.time())
        self.coordinator.checkpoint(swarm_id)
        self.coordinator.append_event(swarm_id, "SWARM_STARTED", details={"revision": plan.revision})

        semaphore = asyncio.Semaphore(max(1, int(plan.max_concurrency)))
        scheduler = SwarmScheduler(plan)
        governor = get_swarm_resource_governor()
        active: set[asyncio.Task] = set()
        previous_completed = completed_count
        no_progress_rounds = 0
        plan.round = 0

        async def execute_node(task_node: SwarmTaskNode, *, backup: bool = False) -> None:
            # Capture the claim before entering the provider thread.  A retry,
            # cancellation, or backup can replace it while the call is running.
            lease_id = task_node.lease_id
            worker = self._build_worker(task_node)
            try:
                context = self.coordinator.task_context(swarm_id, task_node)
                set_context = getattr(worker, "set_context", None)
                if callable(set_context):
                    set_context(context)
                async with semaphore:
                    with self.coordinator.state_lock:
                        if plan.status in {"cancelled", "budget_exhausted", "stalled"} or is_terminal_swarm_status(plan.status):
                            return
                        if task_node.lease_id != lease_id or task_node.state not in {
                            TaskNodeState.RUNNING,
                            TaskNodeState.STRAGGLING,
                        }:
                            return
                    outcome = await asyncio.to_thread(worker.execute_task, task_node, plan)
                if not isinstance(outcome, dict):
                    raise RuntimeError(f"worker returned {type(outcome).__name__}, expected a mapping")
                usage = outcome.get("usage") if isinstance(outcome.get("usage"), dict) else {}
                input_tokens = max(0, int(usage.get("input_tokens", 0) or 0))
                output_tokens = max(0, int(usage.get("output_tokens", 0) or 0))
                tool_calls = max(0, int(outcome.get("tool_calls", 1) or 0))
                claim_grounding: dict[str, Any] | None = None
                grounding_text = str(outcome.get("summary") or "")
                if grounding_text.strip():
                    try:
                        # Deterministic claim grounding, off the event loop.
                        # Computed here but written only after lease acceptance
                        # (below); a grounding failure omits the field rather
                        # than inventing a verdict.
                        claim_grounding = await asyncio.to_thread(ground_claims, grounding_text)
                    except Exception:
                        logger.warning("Swarm claim grounding failed", exc_info=True)
                with self.coordinator.state_lock:
                    updated = scheduler.mark_completed(
                        task_node.task_id,
                        result_summary=str(outcome.get("summary", "Completed successfully.")),
                        evidence=outcome.get("evidence"),
                        output_artifacts=outcome.get("artifacts"),
                        lease_id=lease_id,
                        result_payload=outcome.get("result_payload") if isinstance(outcome.get("result_payload"), dict) else None,
                    )
                    if updated is None or updated.state != TaskNodeState.COMPLETED:
                        # The provider call already happened and the provider
                        # already reported what it cost.  Losing the lease race
                        # only decides which RESULT is authoritative; it does
                        # not un-spend the tokens.  Dropping the measurement
                        # here made ``used_tokens``/``used_tool_calls``
                        # under-report real spend, so the hard budget stopped
                        # bounding the run it was meant to bound.
                        plan.budget.record_usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            tool_calls=tool_calls,
                        )
                        task_node.token_usage = {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                        }
                        task_node.tool_calls += tool_calls
                        self.coordinator.append_event(
                            swarm_id,
                            "TASK_RESULT_FENCED",
                            task_id=task_node.task_id,
                            worker=task_node.assigned_worker or "ephemeral-worker",
                            details={
                                "backup": backup,
                                "lease_id": lease_id,
                                "reason": "lease is no longer current or task is terminal",
                                # The discarded attempt is disclosed, not hidden.
                                "discarded_usage": {
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "total_tokens": input_tokens + output_tokens,
                                    "tool_calls": tool_calls,
                                },
                            },
                        )
                        return
                    receipt_verdict = validate_receipt_verdict(outcome.get("citation_audit"))
                    if receipt_verdict is not None:
                        # This field is populated only from the internal worker
                        # outcome after lease acceptance. External task-complete
                        # payloads can submit generic evidence but cannot assert
                        # this server-generated citation audit.
                        updated.citation_audit = dict(receipt_verdict)
                    plan.budget.record_task_success()
                    governor.record_success(self.provider)
                    updated.token_usage = {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    }
                    updated.tool_calls += tool_calls
                    plan.budget.record_usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        tool_calls=tool_calls,
                    )
                    plan.metrics["completed_tasks"] = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED)
                    plan.metrics["last_usage"] = updated.token_usage
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_COMPLETED",
                        task_id=task_node.task_id,
                        worker=task_node.assigned_worker or "ephemeral-worker",
                        details={"summary": updated.result_summary, "backup": backup, "usage": updated.token_usage},
                        idempotency_key=f"task-complete:{swarm_id}:{task_node.task_id}:{lease_id or task_node.attempts}",
                    )
                    try:
                        from alpha.swarm.memory import get_swarm_memory_manager

                        get_swarm_memory_manager().record_task_result(
                            swarm_id,
                            task_node.task_id,
                            summary=updated.result_summary or "",
                            evidence=list(updated.evidence),
                            artifacts=list(updated.output_artifacts),
                        )
                    except Exception:
                        # Memory promotion is a separate tier; a memory outage
                        # must not turn a real worker result into a fake retry.
                        logger.exception("Failed to record swarm task result in shared memory")
                    try:
                        self.coordinator.publish_message(
                            swarm_id,
                            topic="results",
                            sender=task_node.assigned_worker or f"worker:{task_node.task_id}",
                            kind="task_result",
                            content=updated.result_summary or "",
                            data={"task_id": task_node.task_id, "artifacts": list(updated.output_artifacts), "usage": updated.token_usage},
                            task_id=task_node.task_id,
                            trust="internal",
                            idempotency_key=f"result-message:{swarm_id}:{task_node.task_id}:{lease_id or task_node.attempts}",
                            # The runner already marks the plan dirty for this
                            # transition; forcing a durable full-plan rewrite
                            # here as well is what made a large plan's write
                            # volume quadratic.  The round-boundary flush makes
                            # it durable.
                            durable=False,
                        )
                    except ValueError:
                        # A concurrent cancel/budget stop is authoritative; the
                        # task result was already fenced above if necessary.
                        pass
                # Claim grounding runs AFTER the completion commit.  Running
                # the grounding round trip while the node still read RUNNING
                # widened the window the watchdog samples for stragglers, so a
                # healthy worker already on its way back could be classified
                # as slow and a duplicate provider attempt launched.  Computed
                # here, written under the lock below, still only from the
                # internal outcome and only after lease acceptance; a
                # grounding failure omits the field rather than inventing a
                # verdict.
                claim_grounding: dict[str, Any] | None = None
                grounding_text = str(outcome.get("summary") or "")
                if grounding_text.strip():
                    try:
                        claim_grounding = await asyncio.to_thread(ground_claims, grounding_text)
                    except Exception:
                        logger.warning("Swarm claim grounding failed", exc_info=True)
                with self.coordinator.state_lock:
                    if updated.state == TaskNodeState.COMPLETED:
                        if claim_grounding is not None:
                            # Same discipline as the citation audit: written
                            # only here, from the internal outcome, after lease
                            # acceptance. External completion can never
                            # populate it.
                            updated.claim_grounding = dict(claim_grounding)
                        SwarmAggregator.apply_acceptance_verification(
                            updated,
                            outcome.get("evidence") if isinstance(outcome.get("evidence"), (list, tuple)) else [],
                        )
                # Mark the plan dirty rather than rewriting the whole snapshot
                # here.  This runs once per node, and ``checkpoint`` serialises
                # the entire plan and fsyncs it under the state lock, so a
                # per-node write makes total write volume quadratic in the node
                # count -- measured at 81% of a 60-node plan's wall clock.  The
                # round-boundary ``flush_checkpoint`` above makes it durable, and
                # the ``finally`` flush plus the terminal checkpoint guarantee a
                # dirty plan is never abandoned unwritten.
                self.coordinator.mark_checkpoint_dirty(swarm_id)
            except asyncio.CancelledError:
                # Two different things arrive here.  (a) An authoritative stop
                # -- ``cancel_swarm`` / ``_finalize_budget`` already moved the
                # node to a terminal state, so there is nothing to release.
                # (b) A cancellation scoped to THIS node's execution (the worker
                # itself raised ``CancelledError``, or the await was torn down).
                # Re-raising without touching the node left it in RUNNING
                # holding a live lease, so the node was unreapable until the
                # lease expired, and the eventual terminal error was the
                # misleading "orphaned: execution disappeared while the swarm
                # was idle" instead of the real cause.  ``mark_failed``
                # releases the lease and honours ``max_attempts``, so this
                # cannot livelock either.
                with self.coordinator.state_lock:
                    authoritative_stop = plan.status in {"cancelled", "budget_exhausted", "stalled"} or is_terminal_swarm_status(plan.status)
                    stranded = task_node.state not in {
                        TaskNodeState.COMPLETED,
                        TaskNodeState.FAILED,
                        TaskNodeState.CANCELLED,
                    }
                    if stranded and not authoritative_stop and not backup:
                        scheduler.mark_failed(
                            task_node.task_id,
                            "cancelled: task execution was cancelled before it produced a result",
                            lease_id=lease_id,
                        )
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_CANCELLED",
                        task_id=task_node.task_id,
                        worker=task_node.assigned_worker or "ephemeral-worker",
                        details={
                            "backup": backup,
                            "lease_id": lease_id,
                            "authoritative_stop": authoritative_stop,
                            "released_node": stranded and not authoritative_stop,
                        },
                    )
                raise
            except Exception as exc:
                logger.exception("Error executing task %s in swarm %s", task_node.task_id, swarm_id)
                with self.coordinator.state_lock:
                    if backup:
                        self.coordinator.append_event(
                            swarm_id,
                            "SPECULATIVE_BACKUP_FAILED",
                            task_id=task_node.task_id,
                            details={"error": str(exc), "lease_id": lease_id},
                        )
                    else:
                        error_text = str(exc).lower()
                        if "429" in error_text or "rate limit" in error_text or "too many requests" in error_text:
                            governor.record_rate_limit(self.provider)
                        retry_delay = governor.get_backoff_delay(self.provider)
                        updated = scheduler.mark_failed(
                            task_node.task_id,
                            str(exc),
                            lease_id=lease_id,
                            retry_backoff_seconds=retry_delay,
                        )
                        # Only a TERMINAL failure counts toward the plan-wide
                        # consecutive-failure circuit. State PENDING here means
                        # TASK_RETRY_SCHEDULED (see the branch below) -- the task
                        # has not given up, and its per-task retries are already
                        # bounded by SwarmTaskNode.max_attempts. Charging a
                        # requeue to the shared counter let ONE flaky task burn
                        # the whole plan's budget, after which _finalize_budget
                        # CANCELLED every healthy sibling and set a sticky
                        # terminal budget_exhausted that no resume can reverse.
                        if updated is not None and updated.state == TaskNodeState.FAILED:
                            plan.budget.record_task_failure()
                        if updated is not None and updated.state in {TaskNodeState.PENDING, TaskNodeState.FAILED}:
                            # Record the incident for EVERY failed attempt, and
                            # for EVERY worker.  Two gates used to hide real
                            # failures from the audit trail: ``assigned_worker``
                            # (ephemeral subagents are the majority of a
                            # decomposed plan -- every map shard, every
                            # debate/ensemble branch -- so a hard failure there
                            # produced an empty incident ledger and
                            # ``GET /api/swarms/{id}/incidents`` answered "no
                            # failure incidents" about a swarm that had just
                            # failed a node), and the retryable state only (so
                            # the attempt that finally EXHAUSTED ``max_attempts``
                            # -- the one that actually matters -- was never
                            # recorded at all).  Succession recovery still
                            # applies only where a retry and a successor both
                            # exist; see
                            # ``SwarmIncidentManager.record_failure_and_recover``.
                            from alpha.swarm.incidents import get_swarm_incident_manager

                            retry_scheduled = updated.state == TaskNodeState.PENDING
                            incident = get_swarm_incident_manager().record_failure_and_recover(
                                swarm_id,
                                task_node,
                                str(exc),
                                allow_retry=retry_scheduled,
                            )
                            self.coordinator.append_event(
                                swarm_id,
                                "SWARM_INCIDENT_RECOVERED" if incident.resolved else "SWARM_INCIDENT_UNRESOLVED",
                                task_id=task_node.task_id,
                                details={
                                    "incident_id": incident.incident_id,
                                    "successor": incident.assigned_successor,
                                    "failed_worker": incident.failed_worker,
                                    "worker_type": incident.worker_type,
                                    "attempt": incident.attempt,
                                    "retry_scheduled": retry_scheduled,
                                    "reason": incident.reason,
                                },
                            )
                            event_type = "TASK_RETRY_SCHEDULED" if retry_scheduled else "TASK_FAILED"
                        else:
                            event_type = "TASK_FAILED"
                        self.coordinator.append_event(
                            swarm_id,
                            event_type,
                            task_id=task_node.task_id,
                            details={"error": str(exc), "lease_id": lease_id, "retry_after_seconds": retry_delay},
                        )
                self.coordinator.mark_checkpoint_dirty(swarm_id)
            finally:
                # The scratchpad is intentionally not exposed as a second state
                # source; workers may use it only for their invocation.
                self.coordinator.mark_checkpoint_dirty(swarm_id)

        def launch(task_node: SwarmTaskNode, *, backup: bool = False) -> None:
            task = asyncio.create_task(
                execute_node(task_node, backup=backup),
                name=f"swarm-node-{swarm_id}-{task_node.task_id}",
            )

            def _reap(done: asyncio.Task) -> None:
                active.discard(done)
                if not done.cancelled() and done.exception() is not None:
                    logger.warning("swarm %s node %s task crashed: %s", swarm_id, task_node.task_id, done.exception())

            task.add_done_callback(_reap)
            active.add(task)

        try:
            while plan.status in {"running", "paused"}:
                if plan.status == "paused":
                    await asyncio.sleep(self.poll_interval)
                    continue

                budget_snapshot = plan.budget.check()
                if budget_snapshot.get("exhausted"):
                    return await self._finalize_budget(plan, active)

                plan.round += 1
                plan.metrics["round"] = plan.round
                if scheduler.is_swarm_finished() and not active and not (plan.auto_replan and any(task_node.state == TaskNodeState.FAILED for task_node in plan.tasks.values())):
                    break

                watchdog_report = SwarmWatchdog.check_and_reconcile(
                    plan,
                    lease_seconds=self.lease_seconds,
                )
                for tid in watchdog_report.get("speculative_backups_spawned", []):
                    node = plan.tasks.get(tid)
                    if node is None or node.state not in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                        continue
                    self.coordinator.append_event(swarm_id, "SPECULATIVE_BACKUP_LAUNCHED", task_id=tid)
                    launch(node, backup=True)

                # Renew only tasks whose asyncio execution is still active.  A
                # crashed provider is not kept alive by a blind heartbeat.
                for task_node in plan.tasks.values():
                    if task_node.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING) and task_node.lease_id:
                        if any(active_task.get_name() == f"swarm-node-{swarm_id}-{task_node.task_id}" for active_task in active):
                            scheduler.renew_lease(task_node.task_id, task_node.lease_id, lease_seconds=self.lease_seconds)

                effective_limit = governor.get_effective_concurrency(plan.max_concurrency, self.provider)
                backoff = governor.get_backoff_delay(self.provider)
                if backoff:
                    plan.metrics["provider_backoff_seconds"] = backoff
                    await asyncio.sleep(min(backoff, self.poll_interval * 2))
                ready_tasks = scheduler.dispatch_ready_tasks(
                    self.lease_seconds,
                    lease_owner=f"runner:{swarm_id}",
                    max_dispatch=max(0, effective_limit - len(active)),
                )
                plan.metrics["effective_concurrency"] = effective_limit
                for task_node in ready_tasks:
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_DISPATCHED",
                        task_id=task_node.task_id,
                        worker=task_node.assigned_worker or "ephemeral-worker",
                        details={"objective": task_node.objective, "lease_id": task_node.lease_id, "attempt": task_node.attempts},
                    )
                    launch(task_node)

                # A terminal failure can leave no active task to trigger the
                # reflection branch below.  Attempt the bounded deterministic
                # repair before declaring the DAG stranded or aggregating it.
                auto_replan = getattr(self.coordinator, "auto_replan_failed_task", None)
                if callable(auto_replan) and plan.auto_replan:
                    repair_added = False
                    for failed_task_id in (task_node.task_id for task_node in plan.tasks.values() if task_node.state == TaskNodeState.FAILED):
                        if auto_replan(swarm_id, failed_task_id):
                            repair_added = True
                            break
                    if repair_added:
                        continue

                if not active:
                    if not scheduler.is_swarm_finished():
                        stranded = scheduler.fail_unrunnable_tasks()
                        if stranded:
                            self.coordinator.append_event(
                                swarm_id,
                                "SWARM_STRANDED_TASKS_FAILED",
                                details={"task_ids": [task.task_id for task in stranded]},
                            )
                    if not scheduler.is_swarm_finished():
                        for task_node in plan.tasks.values():
                            if task_node.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                                task_node.state = TaskNodeState.FAILED
                                task_node.error_message = "orphaned: execution disappeared while the swarm was idle"
                                task_node.completed_at = time.time()
                                # A terminal node must never keep a live
                                # dispatch right.  This branch was the one
                                # terminal transition in the runner that did
                                # NOT release the lease, so the checkpoint and
                                # every API projection reported a FAILED task
                                # that still claimed to own an unexpired lease
                                # (and the next reconcile pass would then treat
                                # it as straggling work).  ``fail_unrunnable_tasks``,
                                # ``cancel_swarm`` and ``_finalize_budget`` all
                                # already clear these fields here.
                                task_node.lease_id = None
                                task_node.lease_owner = None
                                task_node.lease_expires_at = None
                                task_node.next_attempt_at = None
                                task_node.backup_worker_launched = False
                                self.coordinator.append_event(
                                    swarm_id,
                                    "TASK_FAILED",
                                    task_id=task_node.task_id,
                                    details={"error": task_node.error_message},
                                )
                    # ORDERING FIX. The two passes above cannot reap a dependent
                    # between them: `fail_unrunnable_tasks` only fails a PENDING
                    # task once one of its dependencies is FAILED or CANCELLED,
                    # and it ran while the orphans were still RUNNING. Only the
                    # second pass - the one above - makes them FAILED, so a
                    # reducer depending on them became unrunnable exactly when
                    # nothing was left to notice. `break` then exited with the
                    # plan non-terminal: `POST /run-async` had already answered
                    # 200 `runner_status: "running"`, the revision froze, and
                    # `status` stayed `running` forever with a task that nothing
                    # would ever dispatch. Reproduced on a 3-item batch:
                    # step -> run-async -> map-1/2/3 failed, reduce pending/0,
                    # revision pinned, status `running` at t+45s.
                    #
                    # Reaping again AFTER the orphans are terminal closes that
                    # window, which in turn lets `is_swarm_finished()` answer
                    # truthfully so the `break` below is actually earned.
                    if not scheduler.is_swarm_finished():
                        newly_stranded = scheduler.fail_unrunnable_tasks()
                        if newly_stranded:
                            self.coordinator.append_event(
                                swarm_id,
                                "SWARM_STRANDED_TASKS_FAILED",
                                details={
                                    "task_ids": [task.task_id for task in newly_stranded],
                                    "reason": "dependency failed while the swarm was idle",
                                },
                            )
                    break

                completed_now = sum(1 for task_node in plan.tasks.values() if task_node.state == TaskNodeState.COMPLETED)
                prior_completed = previous_completed
                if completed_now > prior_completed:
                    previous_completed = completed_now
                    no_progress_rounds = 0
                else:
                    no_progress_rounds += 1
                # ``SwarmReflector`` treats ``previous_completed`` as an
                # optional comparison input.  The runner has already counted
                # this tick, so pass ``None`` to avoid counting the round twice.
                reflection = self.reflector.reflect(
                    plan,
                    previous_completed=None,
                    no_progress_rounds=no_progress_rounds,
                )
                plan.metrics["reflection"] = reflection.to_dict()
                if reflection.recommended_action == "replan":
                    repair_task_id = None
                    auto_replan = getattr(self.coordinator, "auto_replan_failed_task", None)
                    if callable(auto_replan):
                        for failed_task_id in reflection.failed_task_ids:
                            repair_task_id = auto_replan(swarm_id, failed_task_id)
                            if repair_task_id:
                                break
                    if repair_task_id:
                        plan.metrics["last_auto_replan_task_id"] = repair_task_id
                        continue
                if reflection.recommended_action == "stall" and not active:
                    plan.status = "stalled"
                    plan.terminal_reason = reflection.reason
                    self.coordinator.append_event(swarm_id, "SWARM_STALLED", details=reflection.to_dict())
                    self.coordinator.checkpoint(swarm_id)
                    break

                # One durable plan snapshot per scheduler round.  Node
                # transitions marked the plan dirty instead of writing the
                # whole file each time (see
                # ``SwarmCoordinator.mark_checkpoint_dirty``); the round
                # boundary is where the snapshot is made durable, so a crash
                # can lose at most one round of node states and the
                # append-only event journal still holds every transition.
                self.coordinator.flush_checkpoint(swarm_id)
                await asyncio.wait(set(active), timeout=self.poll_interval, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if active:
                self._stop_active_tasks(active)
                await asyncio.gather(*active, return_exceptions=True)
            # The loop may have exited through a ``break`` (which leaves the
            # post-loop terminal handling to flush) or through an exception.
            # Flushing here is a no-op on a clean plan and guarantees a dirty
            # plan is never abandoned unwritten.
            self.coordinator.flush_checkpoint(swarm_id)

        if plan.status == "budget_exhausted":
            return await self._finalize_budget(plan, set())
        if plan.status == "stalled":
            return {"status": plan.status, "aggregated": False, "reason": plan.terminal_reason}
        if plan.status == "cancelled":
            self.coordinator.checkpoint(swarm_id)
            return {"status": plan.status, "aggregated": False, "reason": plan.terminal_reason}

        if plan.status == "running" and scheduler.is_swarm_finished():
            with self.coordinator.state_lock:
                plan.status = "aggregating"
                self.coordinator.append_event(swarm_id, "SWARM_AGGREGATING")
                try:
                    agg_result = SwarmAggregator.aggregate(plan)
                except Exception as exc:
                    logger.exception("Failed to aggregate swarm %s", swarm_id)
                    plan.status = "failed"
                    plan.terminal_reason = f"aggregation_failed: {exc}"
                    plan.final_result = f"Swarm aggregation failed: {exc}"
                    plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self.coordinator.append_event(
                        swarm_id,
                        "SWARM_AGGREGATION_FAILED",
                        details={"error": str(exc)},
                    )
                    self.coordinator.checkpoint(swarm_id)
                    return {"status": plan.status, "aggregated": False, "error": str(exc)}
                plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self.coordinator.append_event(
                    swarm_id,
                    _final_event_for(plan.status),
                    details=agg_result,
                )
                self.coordinator.checkpoint(swarm_id)
            return {"status": plan.status, "aggregated": True, "result": agg_result}

        self.coordinator.checkpoint(swarm_id)
        return {
            "status": plan.status,
            "tasks_completed": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED),
        }

    def start_background_swarm(self, swarm_id: str) -> asyncio.Task | threading.Thread:
        """Launch one background loop per swarm, idempotently in this process.

        Gateway callers already have an event loop.  The synchronous ``swarm``
        tool may be invoked from a worker thread, so provide a daemon-thread
        fallback rather than failing with ``asyncio.create_task()`` requiring a
        running loop.  Both paths use the same coordinator and runner.
        """

        with _ACTIVE_SWARM_REGISTRY_LOCK:
            existing_task = _ACTIVE_SWARM_TASKS.get(swarm_id)
            if existing_task is not None and not existing_task.done():
                return existing_task
            existing_thread = _ACTIVE_SWARM_THREADS.get(swarm_id)
            if existing_thread is not None and existing_thread.is_alive():
                return existing_thread

            try:
                asyncio.get_running_loop()
            except RuntimeError:

                def run_in_thread() -> None:
                    try:
                        asyncio.run(self.run_swarm_async(swarm_id))
                    except Exception:
                        logger.exception("Background swarm runner failed for %s", swarm_id)
                    finally:
                        with _ACTIVE_SWARM_REGISTRY_LOCK:
                            if _ACTIVE_SWARM_THREADS.get(swarm_id) is threading.current_thread():
                                _ACTIVE_SWARM_THREADS.pop(swarm_id, None)

                thread = threading.Thread(
                    target=run_in_thread,
                    name=f"swarm-runner-{swarm_id}",
                    daemon=True,
                )
                _ACTIVE_SWARM_THREADS[swarm_id] = thread
                self._active_tasks[swarm_id] = thread
                thread.start()
                return thread

            task = asyncio.create_task(self.run_swarm_async(swarm_id), name=f"swarm-runner-{swarm_id}")
            _ACTIVE_SWARM_TASKS[swarm_id] = task
            self._active_tasks[swarm_id] = task

            def _cleanup(done: asyncio.Task, sid: str = swarm_id) -> None:
                with _ACTIVE_SWARM_REGISTRY_LOCK:
                    if _ACTIVE_SWARM_TASKS.get(sid) is done:
                        _ACTIVE_SWARM_TASKS.pop(sid, None)

            task.add_done_callback(_cleanup)
            return task

    def shutdown(self, timeout: float = 5.0) -> dict[str, Any]:
        """Stop in-flight swarms and record that they were interrupted.

        Daemon threads are not joined at interpreter exit, so without this a
        Gateway restart kills a running swarm with no event and no checkpoint:
        the plan stays ``running`` on disk and nothing explains why it stopped.
        Every swarm that was active here is cancelled/joined and then marked
        ``paused`` with ``terminal_reason="gateway_shutdown"``, so a later boot
        can resume it deliberately instead of guessing what happened.

        Returns the swarm ids stopped, and those whose thread refused to join
        within the timeout (still marked paused — a plan claiming to run with no
        live worker is the failure this prevents).
        """
        self._shutting_down = True
        deadline = time.time() + max(0.0, float(timeout))
        with _ACTIVE_SWARM_REGISTRY_LOCK:
            tasks = dict(_ACTIVE_SWARM_TASKS)
            threads = dict(_ACTIVE_SWARM_THREADS)

        for task in tasks.values():
            task.cancel()

        forced: list[str] = []
        for swarm_id, thread in threads.items():
            remaining = deadline - time.time()
            if remaining > 0:
                thread.join(remaining)
            if thread.is_alive():
                forced.append(swarm_id)

        stopped: list[str] = []
        for swarm_id in set(tasks) | set(threads):
            with self.coordinator.state_lock:
                plan = self.coordinator.get_swarm(swarm_id)
                if plan is None or is_terminal_swarm_status(plan.status):
                    continue
                if plan.status != "paused":
                    plan.status = "paused"
                    plan.terminal_reason = "gateway_shutdown"
                    self.coordinator.append_event(
                        swarm_id,
                        "SWARM_INTERRUPTED",
                        details={"reason": "gateway_shutdown", "forced": swarm_id in forced},
                    )
                    self.coordinator.checkpoint(swarm_id)
                stopped.append(swarm_id)

        with _ACTIVE_SWARM_REGISTRY_LOCK:
            for swarm_id in stopped:
                _ACTIVE_SWARM_THREADS.pop(swarm_id, None)
                task = _ACTIVE_SWARM_TASKS.get(swarm_id)
                if task is not None and task.done():
                    _ACTIVE_SWARM_TASKS.pop(swarm_id, None)
        self._active_tasks.clear()
        return {"stopped": stopped, "forced": forced, "shutting_down": True}
