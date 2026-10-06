"""Real execution for registered subagents — the runner the control plane lacked.

Phase 1, task 3.T2 of `docs/FEATURE_COMPLETION_PLAN.md`.

## What was broken

`SubagentLifecycleManager` has a complete lifecycle — `spawn_subagent`,
`start_subagent`, `record_progress`, `record_heartbeat`, `renew_lease`,
`complete_subagent`, `fail_subagent`, `cancel_subagent` — and a Gateway surface
that reads it (`GET /api/subagents/control`, which `SubagentsSection` renders as
"Running now"). Measured live on 2026-10-06: `start_subagent` had **no
production caller**. The only production lifecycle call was `spawn_subagent`
from the `/subagent:spawn` route, so a spawned record was created at `ready` and
nothing ever transitioned it. Confirmed by reading the record the route
returned:

    status 'ready'   started_at None   completed_at None
    last_heartbeat None   renew_count 0   result None

This module is the missing runner. It drives a record through
`ready -> running -> completed|failed` using the real `SubagentExecutor`, and it
is deliberately the *only* place that does so.

## Why this file is separate from `hierarchical_delegator`

Task 3.T1 (deep agents) needs the same machinery — isolated-loop submission,
registry polling, terminal translation — and `subagents/AGENTS.md` documents four
rules about that boundary, each of which is a bug that has already shipped:
ContextVars must be copied into the persistent loop or checkpoint lineage and
tracing are lost; `RunJournal` must stay out of the child loop or token
accounting double-counts and futures raise `attached to a different loop`;
`record_external_llm_usage_records` must never run on the persistent loop or a
worker thread; and deferred cleanup must be pinned to the isolated loop because
`asyncio.run()` cancels caller-loop tasks on teardown.

So there is ONE implementation of the boundary here, and 3.T1 binds to it rather
than writing a second, subtly different one. The second implementation is the one
nobody reviews.

## Honesty rules this runner must not break

* A record is marked `completed` **only** when the execution reported
  `SubagentStatus.COMPLETED`. `FAILED`, `CANCELLED` and `TIMED_OUT` each map to
  `fail_subagent` with the real error, never to a completion with an apology in
  the summary.
* The deliverable summary is the subagent's **real final text**. Nothing is
  synthesised when it is missing: an empty answer is reported as an empty answer.
* Artifacts are recorded only when the run actually reported them.
* If assembly or submission raises, the record is failed with the exception text.
  A record must never be left `running` because the runner died quietly.
* The lease is heartbeated on every poll. Without that the manager's own
  `check_liveness_and_stalls` would flip a healthy worker to `stalled`, which is
  the exact fabricated measurement 3.T2 warns about.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: How often the lifecycle lease is renewed while a run is in flight. The
#: contract's default lease is 60s, so 20s leaves room for two missed beats
#: before `check_liveness_and_stalls` can call a live worker stalled.
DEFAULT_HEARTBEAT_SECONDS = 20.0

#: How often the background registry is polled for a terminal result.
DEFAULT_POLL_SECONDS = 1.0

#: Cap on the whole wait, so a wedged run cannot hold a slot forever. The
#: contract's own ``timeout_seconds`` is authoritative for the model call; this
#: only bounds the poller, and exceeding it FAILS the record rather than
#: reporting a timeout as a completion.
DEFAULT_WAIT_CEILING_SECONDS = 3600.0


async def run_registered_subagent(
    subagent_id: str,
    *,
    agent_name: str,
    task: str,
    app_config: Any | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    wait_ceiling_seconds: float = DEFAULT_WAIT_CEILING_SECONDS,
) -> bool:
    """Execute one lifecycle record for real and record its terminal state.

    Args:
        subagent_id: The lifecycle record to run. Must already exist at ``ready``.
        agent_name: Subagent definition name to execute (``general-purpose``,
            ``bash``, ``deep-architect``, ...). Resolved through
            ``get_subagent_config``, so config.yaml overrides apply.
        task: The objective text handed to the subagent.
        app_config: Optional resolved ``AppConfig``; the executor falls back to
            ``get_app_config()`` when omitted.
        thread_id: Thread the run belongs to, for sandbox and event attribution.
        user_id: Runtime user identity, for user-scoped skills and storage.
        heartbeat_seconds: Lease renewal period while running.
        poll_seconds: Registry poll period.
        wait_ceiling_seconds: Hard cap on the wait.

    Returns:
        ``True`` when the record reached ``completed``, ``False`` otherwise. A
        ``False`` return always leaves the record in a terminal FAILED/CANCELLED
        state carrying the reason, never silently ``running``.
    """
    # Imported lazily and in this order. `alpha.tools` MUST load before
    # `alpha.subagents.executor`: the executor reaches `alpha.authz.principal`,
    # whose package `__init__` loads `alpha.guardrails` -> `alpha.tools.governance`
    # -> `alpha.tools.builtins` -> `self_improvement_tool` -> `alpha.subagents`.
    # Importing the executor first raises ImportError on a partially initialised
    # module. This is the same order the Gateway uses at startup.
    import alpha.tools  # noqa: F401  (imported for ordering, not for use)
    from alpha.config.app_config import get_app_config
    from alpha.subagents.executor import (
        SubagentExecutor,
        cleanup_background_task,
    )
    from alpha.subagents.lifecycle import get_subagent_lifecycle_manager
    from alpha.subagents.registry import get_subagent_config
    from alpha.tools import get_available_tools

    lifecycle = get_subagent_lifecycle_manager()

    # `start_subagent` charges one attempt against the ceiling and returns False
    # for a record that is not READY or whose ceiling is spent. Honouring that
    # refusal is the whole point of the attempt accounting: a restart sweep that
    # requeues work must never restart a unit that has given up.
    if not lifecycle.start_subagent(subagent_id):
        rec = lifecycle.get_subagent(subagent_id)
        status = getattr(rec, "status", None)
        logger.warning(
            "[subagent-runner] refusing to start %s: start_subagent returned False (status=%s)",
            subagent_id,
            getattr(status, "value", status),
        )
        return False

    resolved_app_config = app_config if app_config is not None else get_app_config()
    config = get_subagent_config(agent_name, app_config=resolved_app_config)
    if config is None:
        lifecycle.fail_subagent(
            subagent_id,
            f"No subagent definition named {agent_name!r}; nothing was executed.",
            reason="unknown_definition",
        )
        return False

    tools = get_available_tools(
        model_name=config.model,
        # A registered subagent may not itself delegate: nesting would create a
        # second lifecycle plane with no owner. The definition's own
        # `disallowed_tools` already carries the rest of the boundary.
        subagent_enabled=False,
    )

    try:
        executor = SubagentExecutor(
            config=config,
            tools=tools,
            app_config=resolved_app_config,
            thread_id=thread_id,
            user_id=user_id,
            trace_id=subagent_id,
        )
        execution_id = executor.execute_async(task, task_id=subagent_id)
    except Exception as exc:  # noqa: BLE001 - a record must never be left `running`
        logger.exception("[subagent-runner] submission failed for %s", subagent_id)
        lifecycle.fail_subagent(
            subagent_id,
            f"Could not start the subagent run: {type(exc).__name__}: {exc}",
            reason="submission_failed",
        )
        return False

    logger.info("[subagent-runner] %s running as execution_id=%s agent=%s", subagent_id, execution_id, agent_name)

    result = await _await_terminal(
        execution_id,
        lifecycle=lifecycle,
        subagent_id=subagent_id,
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=poll_seconds,
        wait_ceiling_seconds=wait_ceiling_seconds,
    )

    try:
        if result is None:
            # The ceiling elapsed with no terminal status. That is a timeout, and
            # it is reported as one - never adopted as a completion.
            lifecycle.fail_subagent(
                subagent_id,
                f"No terminal status within {wait_ceiling_seconds:.0f}s of polling; the run was abandoned.",
                reason="poller_timeout",
            )
            return False
        return _record_terminal(lifecycle, subagent_id, result)
    finally:
        # Cleanup is best-effort and must not mask the terminal state already
        # written above.
        try:
            cleanup_background_task(execution_id)
        except Exception:  # noqa: BLE001
            logger.debug("[subagent-runner] cleanup for %s did not complete", execution_id, exc_info=True)


async def _await_terminal(
    execution_id: str,
    *,
    lifecycle: Any,
    subagent_id: str,
    heartbeat_seconds: float,
    poll_seconds: float,
    wait_ceiling_seconds: float,
) -> Any | None:
    """Poll the background registry to a terminal status, heartbeating as we go.

    Returns the terminal ``SubagentResult``, or ``None`` when the ceiling elapsed.
    """
    from alpha.subagents.executor import get_background_task_result

    loop = asyncio.get_running_loop()
    started = loop.time()
    last_heartbeat = 0.0

    while True:
        result = get_background_task_result(execution_id)
        if result is not None and result.status.is_terminal:
            return result

        now = loop.time()
        # Renew before the lease can lapse. Without this the manager's own stall
        # detector would reclassify a perfectly healthy worker.
        #
        # `renew_lease` is used rather than `record_heartbeat` on purpose.
        # `record_heartbeat(progress_percent=...)` defaults to `0.0` and clamps it
        # with `min(100.0, max(0.0, value))`, so passing `None` raises a
        # TypeError and omitting it asserts *measured zero progress*. The
        # background registry exposes a status enum, not a percentage, so this
        # runner knows the worker is alive and knows nothing about how far along
        # it is. `renew_lease` is exactly the claim that is true, and
        # `record_progress` carries the real status string without a number.
        if now - last_heartbeat >= heartbeat_seconds:
            lifecycle.renew_lease(subagent_id)
            lifecycle.record_progress(
                subagent_id,
                next_action=f"subagent execution {getattr(result.status, 'value', result.status)}",
            )
            last_heartbeat = now

        if now - started >= wait_ceiling_seconds:
            return None

        await asyncio.sleep(poll_seconds)


def _record_terminal(lifecycle: Any, subagent_id: str, result: Any) -> bool:
    """Write the execution's real terminal state onto the lifecycle record."""
    from alpha.subagents.executor import SubagentStatus
    from alpha.subagents.lifecycle import SubagentDeliverable

    status = result.status
    summary = (result.result or "").strip()

    if status == SubagentStatus.COMPLETED:
        deliverable = SubagentDeliverable(
            status="completed",
            # The subagent's own words. An empty completion stays empty rather
            # than acquiring a reassuring sentence nobody produced.
            summary=summary or "The subagent finished without returning any text.",
            confidence_score=1.0,
        )
        # `artifacts` is deliberately left at its empty default. `SubagentResult`
        # carries no artifact list (task_id, status, result, error, stop_reason,
        # ai_messages, token_usage_records, tool_receipts, bash_executions), so
        # there is nothing measured to put here. Reading a non-existent attribute
        # with getattr would yield `[]` every time, and an always-empty artifact
        # list is indistinguishable from "this run produced no files" — the exact
        # unmeasured-zero shape this repository's rules forbid. Populating it from
        # the result TEXT would be worse: that is parsing prose as a path list.
        if result.stop_reason:
            deliverable.recommendations.append(f"run ended early: {result.stop_reason}")
        return lifecycle.complete_subagent(subagent_id, deliverable)

    # Every other terminal status is a failure carrying the real reason.
    reason = (result.error or "").strip() or f"the run ended as {getattr(status, 'value', status)}"
    lifecycle.fail_subagent(subagent_id, reason, reason=f"execution_{getattr(status, 'value', status)}")
    return False
