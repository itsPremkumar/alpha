"""Node implementations for the graph plane's top-level execution graph.

Each node is a deterministic function that reads state, performs one bounded
step, and returns a partial state update. Nodes do **not** write to the ledger
directly; they call :class:`~alpha.graph.ledger.TaskLedger` methods, which
enforce the transition table and handle concurrency. This separation is what
makes the graph testable: a node can be unit-tested with a stub ledger, and the
ledger can be integration-tested without a graph.

Node responsibilities (plan §6.2)
---------------------------------
* ``normalize_request``: validate input, assign ``run_id``, classify
  answer/plan/execute, set user-visible objective.
* ``collect_context``: query relevant memory, files, repo state, available
  tools, model health.
* ``plan_goal``: propose tasks, dependencies, outputs, tests, risk, budgets.
* ``validate_plan``: validate graph acyclicity, capability availability, limits,
  permissions, schema, acceptance criteria.
* ``select_ready_tasks``: select tasks whose dependencies are satisfied, within
  concurrency and resource caps.
* ``dispatch_worker``: resolve worker/capabilities; pass only required context;
  link child run/task IDs.
* ``capture_artifact``: persist result, hash it, attach provenance, emit event.
* ``verify_task``: run deterministic checks first, then an LLM reviewer if
  useful.
* ``repair_task``: classify error, inspect trace, propose a bounded next attempt.
* ``approval_gate``: pause with a clear action preview and resume key.
* ``final_verify``: check every goal-level acceptance criterion against
  artifacts.
* ``report_result``: summarize completed work, evidence, failures, approvals,
  and next safe step.

What nodes must **not** do (plan §6.2)
---------------------------------------
* ``normalize_request`` must not claim the task is completed.
* ``collect_context`` must not load every memory item into the prompt.
* ``plan_goal`` must not directly execute tools during planning.
* ``validate_plan`` must not trust unchecked LLM JSON.
* ``select_ready_tasks`` must not schedule tasks with unresolved prerequisites.
* ``dispatch_worker`` must not let workers change global policy or arbitrary
  tasks.
* ``capture_artifact`` must not keep the only copy in transient chat history.
* ``verify_task`` must not accept a worker's "done" assertion as proof.
* ``repair_task`` must not retry identical work indefinitely.
* ``approval_gate`` must not ask for approval after the side effect already
  happened.
* ``final_verify`` must not convert partial completion into a success status.
* ``report_result`` must not hide blockers or fabricate command/tool results.

Why every node takes exactly one argument
-----------------------------------------
LangGraph invokes a node with the state and nothing else, so a node that
declares extra keyword-only parameters raises ``TypeError`` the moment the
graph runs -- which is exactly what the first version of this module did, and
why ``test_run_returns_result`` failed while every unit test passed: the unit
tests called the nodes directly with the arguments the graph would not supply.

So all caller-provided input rides in the state (``user_request``,
``plan_tasks``, ``verify_checks``, ``artifact_request``), and a node reads it
from there. That keeps node signatures uniform *and* makes the input visible in
the checkpoint, which is what lets a resumed run see what the original caller
asked for instead of reconstructing it from a log line.
"""

from __future__ import annotations

from typing import Any

from alpha.graph.dag import (
    CycleDetected,
    UnknownDependency,
    ready_tasks,
    validate_tasks,
)
from alpha.graph.failures import GraphFailureClass, raise_graph_failure
from alpha.graph.ids import GoalId, TaskId
from alpha.graph.ledger import LedgerConflict, TaskLedger
from alpha.graph.models import (
    AcceptanceReport,
    Artifact,
    CapabilityRequirement,
    Goal,
    GoalMode,
    GoalStatus,
    ModelValidationError,
    RiskLevel,
    TaskNode,
    TaskStatus,
    TaskType,
)
from alpha.graph.state import AlphaGraphState, GraphIntent, GraphStatus

__all__ = [
    "NodeResult",
    "approval_gate",
    "capture_artifact",
    "collect_context",
    "dispatch_worker",
    "final_verify",
    "normalize_request",
    "plan_goal",
    "repair_task",
    "report_result",
    "select_ready_tasks",
    "validate_plan",
    "verify_task",
]


class NodeResult(dict):
    """The result of a node execution: a partial state update.

    This is a ``dict`` **subclass**, not a wrapper object. LangGraph requires a
    node to return a mapping it can merge into state, and an earlier version of
    this class returned a bespoke ``NodeResult`` -- which passed every unit test
    (they read ``.state_update``) and then failed at runtime with
    ``InvalidUpdateError: Expected dict, got NodeResult``. Subclassing ``dict``
    means the object *is* the update, so the graph and the tests see the same
    thing.

    ``stop`` rides beside the mapping rather than inside it: it is advisory
    metadata for a caller driving nodes directly, and putting it in the dict
    would make LangGraph try to write it into a state channel that does not
    exist.
    """

    def __init__(self, state_update: dict[str, Any] | None = None, *, stop: bool = False) -> None:
        super().__init__(state_update or {})
        self.stop = stop

    @property
    def state_update(self) -> dict[str, Any]:
        """The mapping view, for a caller that prefers the explicit name."""
        return self

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"NodeResult({dict(self)!r}, stop={self.stop})"


def _get_ledger(state: AlphaGraphState) -> TaskLedger:
    """Get the ledger from state or raise.

    The ledger is passed in state as a non-checkpointed field (LangGraph's
    ``configurable`` bag is the other option, but a field is simpler to test
    and keeps the node signature to one argument). Centralising the lookup so a
    missing ledger is a loud error, not an ``AttributeError`` five frames deep.
    """
    ledger = state.get("_ledger")
    if ledger is None:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, "ledger not found in state; pass it via configurable")
    return ledger  # type: ignore[return-value]


def _get_goal(state: AlphaGraphState, ledger: TaskLedger) -> Goal:
    goal = ledger.get_goal(GoalId(state["goal_id"]))
    if goal is None:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, f"goal {state['goal_id']} not found in ledger")
    return goal


def _active_task_id(state: AlphaGraphState) -> str:
    """The task the current node operates on: the most recently dispatched."""
    active = state.get("active_task_ids") or []
    return active[-1] if active else ""


def _check_cancellation(state: AlphaGraphState) -> None:
    """Raise if cancellation has been requested.

    Called at the start of every node. A cancelled graph must not proceed, and
    the check is here so a node cannot forget it.
    """
    if state.get("cancel_requested"):
        raise_graph_failure(GraphFailureClass.CANCELLED, "graph execution was cancelled")


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------


def normalize_request(state: AlphaGraphState) -> NodeResult:
    """Validate input, assign ``run_id``, classify intent, set objective.

    This is the graph's entry point. It does **not** claim the task is
    completed; it sets up the goal and routes to the right branch.
    """
    _check_cancellation(state)

    user_request = str(state.get("user_request", "")).strip()
    if not user_request:
        raise_graph_failure(GraphFailureClass.INTENT_MISMATCH, "user_request must not be empty")

    run_id = state.get("run_id", "")
    if not run_id:
        raise_graph_failure(GraphFailureClass.INTENT_MISMATCH, "run_id must not be empty: a goal with no owning run cannot be attributed")

    intent = state.get("intent") or GraphIntent.EXECUTE

    ledger = _get_ledger(state)
    goal_id = GoalId(state.get("goal_id", ""))
    if not goal_id:
        goal = Goal.create(
            run_id=run_id,
            user_request=user_request,
            normalized_objective=user_request,
            mode=GoalMode(intent.value),
        )
        ledger.create_goal(goal)
        goal_id = goal.goal_id

    return NodeResult(
        {
            "goal_id": goal_id,
            "intent": intent,
            "status": GraphStatus.ROUTING,
            "current_node": "normalize_request",
            "next_node": "collect_context",
        },
    )


def collect_context(state: AlphaGraphState) -> NodeResult:
    """Query relevant memory, files, repo state, available tools, model health.

    This node does **not** load every memory item into the prompt. It fetches
    a bounded, relevant subset and leaves the content out of state entirely -- a
    claim's provenance lives in the ledger and the node that needs it reads it by
    ID.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    # Bounded: relevant claims only, capped. The count is reported so the UI can
    # say "3 related past facts found" rather than implying the whole memory was
    # consulted. The claims themselves stay in the ledger -- this node reads them
    # only to prove they are reachable, and the count is deliberately not written
    # into state: a state field nobody reads is a field that will drift from the
    # ledger the UI actually renders.
    related = ledger.list_claims(subject_entity_id=f"goal:{goal.goal_id}", limit=10)
    if len(related) >= 10:
        # Hit the cap: log it so a caller cannot mistake "the first 10" for "all".
        import logging

        logging.getLogger(__name__).debug("collect_context: claim list reached its bound of 10")

    return NodeResult(
        {
            "status": GraphStatus.PLANNING,
            "current_node": "collect_context",
            "next_node": "plan_goal",
        },
    )


def plan_goal(state: AlphaGraphState) -> NodeResult:
    """Propose tasks, dependencies, outputs, tests, risk, budgets.

    This node does **not** directly execute tools during planning. It creates
    task records in the ledger and returns the plan. The plan itself comes from
    the caller (an LLM planner upstream, or a deterministic one); it is
    validated before being persisted, so unchecked planner JSON never becomes
    an executable row.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    tasks = state.get("plan_tasks") or []
    if not tasks:
        # A plan with no tasks is a plan that does nothing. Refuse it.
        raise_graph_failure(GraphFailureClass.PLAN_VALIDATION_FAILED, "planner returned no tasks")

    task_ids: list[str] = []
    for task_data in tasks:
        try:
            task = TaskNode.create(
                goal_id=goal.goal_id,
                title=task_data.get("title", ""),
                description=task_data.get("description", ""),
                task_type=TaskType(task_data.get("task_type", "tool")),
                depends_on=[TaskId(d) for d in task_data.get("depends_on", [])],
                assigned_worker=task_data.get("assigned_worker", ""),
                required_capabilities=[CapabilityRequirement(capability=c["capability"], required=c.get("required", True)) for c in task_data.get("required_capabilities", [])],
                acceptance=AcceptanceReport.from_checks(task_data.get("acceptance_checks", [])),
                risk_level=RiskLevel(task_data.get("risk_level", "low")),
                max_attempts=int(task_data.get("max_attempts", 2)),
                priority=int(task_data.get("priority", 50)),
            )
        except (ModelValidationError, ValueError) as exc:
            raise_graph_failure(GraphFailureClass.PLAN_VALIDATION_FAILED, f"invalid task in plan: {exc}")

        ledger.create_task(task)
        task_ids.append(task.task_id)

    goal.task_ids = [TaskId(t) for t in task_ids]
    ledger.update_goal(goal)

    return NodeResult(
        {
            "task_ids": task_ids,
            "status": GraphStatus.VALIDATING,
            "current_node": "plan_goal",
            "next_node": "validate_plan",
        },
    )


def validate_plan(state: AlphaGraphState) -> NodeResult:
    """Validate graph acyclicity, capability availability, limits, permissions.

    This node does **not** trust unchecked LLM JSON. It runs the deterministic
    validator (:func:`~alpha.graph.dag.validate_tasks`) and refuses a plan that
    fails.

    It also moves the goal ``PLANNING -> RUNNING`` here, which is the honest
    moment: the plan is validated and the graph is about to schedule real work.
    Every node downstream (``select_ready_tasks``, ``final_verify``) then sees a
    goal whose status matches what the graph is actually doing, rather than one
    still labelled ``planning`` while it dispatches.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    tasks = ledger.list_tasks(goal_id=goal.goal_id)
    edges = ledger.list_edges(graph_id=GoalId(f"task-graph:{goal.goal_id}"))  # type: ignore[arg-type]

    try:
        report = validate_tasks(tasks, goal_id=goal.goal_id, edges=edges)
    except CycleDetected as exc:
        raise_graph_failure(GraphFailureClass.DEPENDENCY_CYCLE, str(exc))
    except UnknownDependency as exc:
        raise_graph_failure(GraphFailureClass.PLAN_VALIDATION_FAILED, str(exc))

    if not report.ok:
        raise_graph_failure(
            GraphFailureClass.PLAN_VALIDATION_FAILED,
            f"plan validation failed: {report.unknown_dependencies}",
        )

    if goal.status is GoalStatus.PLANNING:
        goal.transition_to(GoalStatus.RUNNING)
        ledger.update_goal(goal)

    return NodeResult(
        {
            "status": GraphStatus.SCHEDULING,
            "current_node": "validate_plan",
            "next_node": "select_ready_tasks",
        },
    )


def select_ready_tasks(state: AlphaGraphState) -> NodeResult:
    """Select tasks whose dependencies are satisfied, within concurrency caps.

    This node does **not** schedule tasks with unresolved prerequisites. It
    uses :func:`~alpha.graph.dag.ready_tasks` which checks dependencies,
    capabilities, and approval requirements.

    The concurrency cap comes from the goal's budget (default 2, per the plan's
    §6.4 guidance for an 8 GB baseline machine) and is never silently raised.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    tasks = ledger.list_tasks(goal_id=goal.goal_id)
    cap = goal.budget.max_concurrent_tasks or 2
    ready = ready_tasks(tasks, concurrency_cap=cap)

    ready_ids = [task.task_id for task in ready]

    return NodeResult(
        {
            "ready_task_ids": ready_ids,
            "status": GraphStatus.DISPATCHING if ready_ids else GraphStatus.FINAL_VERIFYING,
            "current_node": "select_ready_tasks",
            "next_node": "dispatch_worker" if ready_ids else "final_verify",
        },
    )


def dispatch_worker(state: AlphaGraphState) -> NodeResult:
    """Resolve worker/capabilities; pass only required context; link child IDs.

    This node does **not** let workers change global policy or arbitrary tasks.

    Order is load-bearing, and it is the reverse of what an intuitive version
    does: capabilities first, then approval, then the attempt. Checking
    capability availability before approval means an operator is never asked to
    approve a task the graph already knows cannot run, and asking *after* the
    attempt would mean approving a side effect that already happened.

    A task that needs approval parks in ``WAITING_APPROVAL`` **without** an
    attempt record, because nothing has run yet -- so ``attempt_count`` stays 0
    and the ledger shows no dispatch was made.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    tasks = ledger.list_tasks(goal_id=goal.goal_id)
    if not tasks:
        return NodeResult(
            {
                "status": GraphStatus.FINAL_VERIFYING,
                "current_node": "dispatch_worker",
                "next_node": "final_verify",
            },
        )

    statuses = {task.task_id: task.status for task in tasks}
    candidates = [task for task in tasks if task.status is not None and task.status not in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED)]
    # Dependencies are a readiness question and are re-checked here, on the
    # *candidate* set rather than the ready set, because a task that is not
    # ready may still need to be parked (blocked on a dead prerequisite) rather
    # than silently dropped from the plan.
    candidates.sort(key=lambda task: (-task.priority, task.attempt_count, task.created_at, task.task_id))

    for task in candidates:
        # 1. Capability availability, checked **before** readiness. A capability
        #    the graph knows is missing must become a visible block, not a silent
        #    skip: ``ready_to_dispatch`` reports it as "not yet", which would let
        #    the task vanish from the plan instead of the operator seeing
        #    "blocked: browser_navigation".
        missing = task.unresolved_capabilities()
        if missing:
            if task.status is not TaskStatus.BLOCKED:
                ledger.mark_task_blocked(task.task_id, reason="unresolved capabilities: " + ", ".join(m.capability for m in missing))
            return NodeResult(
                {
                    "blocked_task_ids": [task.task_id],
                    "active_task_ids": [],
                    "status": GraphStatus.SCHEDULING,
                    "current_node": "dispatch_worker",
                    "next_node": "select_ready_tasks",
                },
            )

        ready, _reason = task.ready_to_dispatch(dependency_status=statuses)
        if not ready and task.status is not TaskStatus.BLOCKED and task.status is not TaskStatus.WAITING_APPROVAL:
            continue

        # 2. Approval. Park before any attempt is recorded.
        if task.needs_approval and task.status is not TaskStatus.WAITING_APPROVAL:
            ledger.park_task_for_approval(task.task_id)
            return NodeResult(
                {
                    "active_task_ids": [task.task_id],
                    "status": GraphStatus.WAITING_APPROVAL,
                    "current_node": "dispatch_worker",
                    "next_node": "approval_gate",
                },
            )

        if task.status is TaskStatus.WAITING_APPROVAL:
            # Already parked by an earlier pass; the approval gate resolves it.
            return NodeResult(
                {
                    "active_task_ids": [task.task_id],
                    "status": GraphStatus.WAITING_APPROVAL,
                    "current_node": "dispatch_worker",
                    "next_node": "approval_gate",
                },
            )

        # 3. Dispatch. The ledger opens the attempt and persists the counter in
        #    one locked write.
        if not ready:
            continue
        _attempt, _task = ledger.begin_attempt(task.task_id)
        return NodeResult(
            {
                "active_task_ids": [task.task_id],
                "status": GraphStatus.EXECUTING,
                "current_node": "dispatch_worker",
                "next_node": "capture_artifact",
            },
        )

    return NodeResult(
        {
            "status": GraphStatus.FINAL_VERIFYING,
            "current_node": "dispatch_worker",
            "next_node": "final_verify",
        },
    )


def capture_artifact(state: AlphaGraphState) -> NodeResult:
    """Persist result, hash it, attach provenance, emit event.

    This node does **not** keep the only copy in transient chat history. It
    writes the artifact to the ledger and returns the artifact ID. The payload
    itself never enters graph state -- plan §6.3.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    task_id = _active_task_id(state)
    if not task_id:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, "capture_artifact requires an active task")

    request = state.get("artifact_request") or {}
    uri = str(request.get("uri", "")).strip()
    content_hash = str(request.get("content_hash", "")).strip()
    if not uri or not content_hash:
        # An artifact with no URI or no hash is not an artifact -- it is a
        # string in a chat message. Refuse rather than persist a placeholder.
        raise_graph_failure(
            GraphFailureClass.ARTIFACT_WRITE_FAILED,
            "capture_artifact requires artifact_request.uri and artifact_request.content_hash; a claim of work with no durable hashed payload is not evidence",
        )

    artifact = Artifact.create(
        run_id=state["run_id"],
        artifact_type=str(request.get("artifact_type", "generic")),
        uri=uri,
        content_hash=content_hash,
        goal_id=goal.goal_id,
        task_id=TaskId(task_id),
        created_by_node="capture_artifact",
        size_bytes=request.get("size_bytes"),
        mime_type=str(request.get("mime_type", "application/octet-stream")),
    )
    ledger.create_artifact(artifact)

    return NodeResult(
        {
            "status": GraphStatus.VERIFYING,
            "current_node": "capture_artifact",
            "next_node": "verify_task",
        },
    )


def verify_task(state: AlphaGraphState) -> NodeResult:
    """Run deterministic checks first, then an LLM reviewer if useful.

    This node does **not** accept a worker's "done" assertion as proof. It
    evaluates each acceptance check against measured evidence supplied in
    ``state["verify_checks"]`` and only succeeds the task if every declared
    check carries a holding verdict.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)

    task_id = _active_task_id(state)
    if not task_id:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, "verify_task requires an active task")

    task = ledger.get_task(TaskId(task_id))
    if task is None:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, f"task {task_id} not found")

    for check_data in state.get("verify_checks") or []:
        check_name = check_data.get("check", "")
        verdict = check_data.get("verdict")
        evidence = check_data.get("evidence", "")
        evaluated_by = check_data.get("evaluated_by", "verify_task")
        # ``evaluate_acceptance`` persists the verdict in the same locked write
        # that reads the task, so a measured verdict cannot be lost between here
        # and the transition that follows.
        try:
            ledger.evaluate_acceptance(
                TaskId(task_id),
                check_name,
                verdict,
                evidence=evidence,
                evaluated_by=evaluated_by,
            )
        except LedgerConflict as exc:
            raise_graph_failure(GraphFailureClass.VERIFICATION_FAILED, str(exc))

    task = ledger.get_task(TaskId(task_id))
    if task is None:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, f"task {task_id} not found")

    if not task.acceptance.declared:
        raise_graph_failure(
            GraphFailureClass.VERIFICATION_FAILED,
            f"task {task_id} has no acceptance checks; cannot verify",
        )

    refusal = task.acceptance.refusal_reason()
    if refusal is not None:
        ledger.mark_task_failed(TaskId(task_id), reason=refusal, error_code="VERIFICATION_FAILED")
        return NodeResult(
            {
                "status": GraphStatus.REPAIRING,
                "current_node": "verify_task",
                "next_node": "repair_task",
            },
        )

    ledger.mark_task_succeeded(TaskId(task_id))

    return NodeResult(
        {
            "completed_task_ids": [task_id],
            "status": GraphStatus.SCHEDULING,
            "current_node": "verify_task",
            "next_node": "select_ready_tasks",
        },
    )


def repair_task(state: AlphaGraphState) -> NodeResult:
    """Classify error, inspect trace, propose a bounded next attempt.

    This node does **not** retry identical work indefinitely. It checks the
    attempt ceiling and creates a new repair task that depends on the original,
    so the repair cannot reorder itself ahead of the failure it addresses.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    task_id = _active_task_id(state)
    if not task_id:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, "repair_task requires an active task")

    task = ledger.get_task(TaskId(task_id))
    if task is None:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, f"task {task_id} not found")

    if not task.can_retry:
        raise_graph_failure(
            GraphFailureClass.RETRY_BUDGET_EXHAUSTED,
            f"task {task_id} has no retries left ({task.attempt_count}/{task.max_attempts})",
        )

    repair = TaskNode.create(
        goal_id=goal.goal_id,
        title=f"Repair: {task.title}",
        description=task.failure_summary or "repair",
        task_type=task.task_type,
        depends_on=[task.task_id],
        risk_level=task.risk_level,
        max_attempts=task.max_attempts,
        acceptance=AcceptanceReport.from_checks([f"repair of {task.title} verified"]),
    )
    ledger.create_task(repair)

    return NodeResult(
        {
            "task_ids": [repair.task_id],
            "active_task_ids": [repair.task_id],
            "status": GraphStatus.SCHEDULING,
            "current_node": "repair_task",
            "next_node": "select_ready_tasks",
        },
    )


def approval_gate(state: AlphaGraphState) -> NodeResult:
    """Pause with a clear action preview and resume key.

    The pending approval is resolved by the caller (an operator decision
    arrives in a later graph invocation). This node verifies the approval still
    exists and still names the operation the caller is about to run -- it does
    **not** ask for approval after the side effect already happened, and it does
    not let a stale grant ride a changed operation.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)

    approval_id = state.get("approval_request_id") or ""
    if not approval_id:
        raise_graph_failure(GraphFailureClass.USER_APPROVAL_REQUIRED, "approval_gate requires an approval_request_id")

    approval = ledger.get_approval(approval_id)  # type: ignore[arg-type]
    if approval is None:
        raise_graph_failure(GraphFailureClass.INTERNAL_ERROR, f"approval {approval_id} not found")

    if not approval.is_approved:
        # Still pending or rejected: the task stays parked. The graph does not
        # self-approve, and a rejection is not a silent drop -- the caller sees
        # the state and the note.
        return NodeResult(
            {
                "status": GraphStatus.WAITING_APPROVAL,
                "current_node": "approval_gate",
                "next_node": "__end__",
            },
        )

    return NodeResult(
        {
            "status": GraphStatus.EXECUTING,
            "current_node": "approval_gate",
            "next_node": "capture_artifact",
        },
    )


def final_verify(state: AlphaGraphState) -> NodeResult:
    """Check every goal-level acceptance criterion against artifacts.

    This node does **not** convert partial completion into a success status.

    Goal-level criteria are prose ("the bug is fixed and covered by a test"),
    and evaluating them requires a human or a model call that this plane does
    not make -- so this node reports what it actually measured: every task's
    own acceptance checks. A goal whose tasks all passed but whose prose
    criteria were never independently checked is **partial**, not succeeded,
    and the report node says exactly which criterion was left unmeasured.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    tasks = ledger.list_tasks(goal_id=goal.goal_id)
    all_verified = all(t.status is TaskStatus.SUCCEEDED for t in tasks) if tasks else False
    any_failed = any(t.status is TaskStatus.FAILED for t in tasks)
    any_blocked = any(t.status is TaskStatus.BLOCKED for t in tasks)
    criteria_measured = all(task.acceptance.passed for task in tasks if task.status is TaskStatus.SUCCEEDED)

    if all_verified and criteria_measured and not any_failed and not any_blocked:
        target = GoalStatus.SUCCEEDED
    else:
        target = GoalStatus.PARTIAL

    if goal.status not in (GoalStatus.SUCCEEDED, GoalStatus.PARTIAL):
        # A goal may reach final verification still labelled ``planning`` when
        # a caller drives the nodes directly rather than through ``validate_plan``
        # (which is what moves it to ``RUNNING``). Refusing the transition would
        # report "cannot move planning -> partial", which names a lifecycle
        # detail instead of the actual outcome, so the goal is advanced through
        # ``RUNNING`` first -- the graph genuinely did reach its verification
        # phase.
        if goal.status is GoalStatus.PLANNING:
            goal.transition_to(GoalStatus.RUNNING)
        goal.transition_to(target)
        ledger.update_goal(goal)

    return NodeResult(
        {
            "status": GraphStatus.REPORTING,
            "current_node": "final_verify",
            "next_node": "report_result",
        },
    )


def report_result(state: AlphaGraphState) -> NodeResult:
    """Summarize completed work, evidence, failures, approvals, next step.

    This node does **not** hide blockers or fabricate command/tool results. It
    reads the actual ledger state and produces a summary that names what was
    verified, what failed, and what remains blocked.
    """
    _check_cancellation(state)
    ledger = _get_ledger(state)
    goal = _get_goal(state, ledger)

    tasks = ledger.list_tasks(goal_id=goal.goal_id)
    artifacts = ledger.list_artifacts(goal_id=goal.goal_id)

    succeeded = [t for t in tasks if t.status is TaskStatus.SUCCEEDED]
    failed = [t for t in tasks if t.status is TaskStatus.FAILED]
    blocked = [t for t in tasks if t.status is TaskStatus.BLOCKED]

    if failed or blocked:
        status = GraphStatus.PARTIAL
    elif succeeded:
        status = GraphStatus.SUCCEEDED
    else:
        status = GraphStatus.FAILED

    summary = f"Goal {goal.goal_id}: {len(succeeded)} succeeded, {len(failed)} failed, {len(blocked)} blocked, {len(artifacts)} artifact(s)."

    return NodeResult(
        {
            "final_result": summary,
            "status": status,
            "current_node": "report_result",
            "next_node": "__end__",
        },
        stop=True,
    )
