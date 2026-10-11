"""The top-level LangGraph execution graph for the graph plane.

This is the graph the plan's §6.1 describes: a bounded, explicit control-flow
graph that routes a user request through normalize → plan → validate →
schedule → dispatch → capture → verify → repair → final-verify → report.

Why a separate graph from the lead agent
-----------------------------------------
The lead agent (``alpha.agents.lead_agent``) is a ReAct loop: model ⇄ tools,
with middleware hooks. It is optimized for *conversational* agent behavior --
chat, tool calls, clarification. The graph plane's execution graph is
optimized for *task orchestration*: explicit task DAGs, bounded loops,
deterministic scheduling, verification gates.

The two are complementary, not competing. A user request that needs a
multi-step plan with verification goes through the graph plane; a simple
question goes through the lead agent. The boundary is the task contract
(:class:`~alpha.graph.models.TaskNode`): the lead agent can be invoked *as a
worker* by :class:`~alpha.graph.adapter.LeadAgentAdapter`, which keeps the
planning authority in the graph and the conversational surface in the agent.

The graph's shape
-----------------
::

    START -> normalize_request -> collect_context -> plan_goal
        -> validate_plan -> select_ready_tasks -> dispatch_worker
        -> capture_artifact -> verify_task -> select_ready_tasks   (loop)
        -> repair_task -> select_ready_tasks                       (loop)
        -> final_verify -> report_result -> END

``dispatch_worker`` routes to ``approval_gate`` for a HIGH-risk task, and
``approval_gate`` returns to ``capture_artifact`` once an operator has
approved the exact operation it was shown.

State, not arguments
--------------------
Every node takes exactly one argument (the state) because that is what
LangGraph supplies. All caller-provided input -- the user request, the planner's
proposed tasks, per-task acceptance verdicts, and the artifact to capture --
rides in the state via :func:`build_initial_state`. That is what lets a
checkpointed run resume and still see what the original caller asked for,
rather than reconstructing it from a log line.

The one exception is the ledger: it rides in ``configurable``, not in state,
because state is checkpointed and a live SQLite store is not a checkpointee --
serialising it would either fail the first checkpoint write or bloat every
checkpoint with a connection object. ``_get_ledger`` resolves ``configurable``
first and falls back to ``state["_ledger"]`` only for callers that drive nodes
directly.
"""

from __future__ import annotations

from typing import Any, Final

from langgraph.graph import START, StateGraph

from alpha.graph.ids import new_graph_id
from alpha.graph.ledger import TaskLedger
from alpha.graph.models import GoalMode
from alpha.graph.nodes import (
    approval_gate,
    capture_artifact,
    collect_context,
    dispatch_worker,
    final_verify,
    normalize_request,
    plan_goal,
    repair_task,
    report_result,
    select_ready_tasks,
    validate_plan,
    verify_task,
)
from alpha.graph.state import AlphaGraphState, GraphIntent, GraphStatus

__all__ = [
    "GraphRuntime",
    "GraphRuntimeConfig",
    "VALID_SUCCESSORS",
    "build_graph",
    "build_graph_runtime",
    "build_initial_state",
    "route_next",
]


#: Every successor a node may name. ``__end__`` is LangGraph's terminal marker,
#: so it is listed here for a single authoritative list rather than being
#: special-cased at each call site.
VALID_SUCCESSORS: Final[frozenset[str]] = frozenset(
    {
        "__end__",
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
    }
)

#: Sentinel a node sets on ``next_node`` when the run is over. Using one
#: constant means "the run finished" is one string the router can refuse to
#: mis-read, instead of ``"__end__"`` sprinkled through every node.
END_MARKER = "__end__"


def _route_next(state: AlphaGraphState) -> str:
    """Return the successor named by ``state['next_node']``.

    This is the graph's single router, and it is **fail-closed**: an unknown
    successor raises instead of falling back to ``END``. A default-to-end would
    silently drop a run whose node named a successor that does not exist, which
    is indistinguishable from a completed run to every caller watching the
    status field -- the exact false-success shape this plane exists to prevent.
    """
    successor = str(state.get("next_node") or "")
    if successor not in VALID_SUCCESSORS:
        raise ValueError(f"node {state.get('current_node')!r} named successor {successor!r}, which is not a graph node; valid successors: {sorted(VALID_SUCCESSORS)}")
    return successor


#: Public alias so a caller driving the graph outside LangGraph (a Gateway route
#: that steps one node at a time) uses the same router.
route_next = _route_next


class GraphRuntimeConfig:
    """Configuration for the graph runtime.

    ``max_concurrency`` is the default concurrency cap for task dispatch. It is
    a **ceiling**, and a per-goal
    :attr:`~alpha.graph.models.GraphBudget.max_concurrent_tasks` may only lower
    it -- a caller cannot widen the cap through the budget.
    """

    def __init__(self, *, max_concurrency: int = 2, enable_checkpoints: bool = True, checkpointer: Any = None) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}; 0 is refused, not clamped to 1")
        self.max_concurrency = max_concurrency
        self.enable_checkpoints = enable_checkpoints
        #: Bound by the Gateway when it holds a LangGraph checkpointer. ``None``
        #: is valid: the ledger is the authoritative store, so a run without a
        #: checkpointer is still durable -- only not resumable mid-graph.
        self.checkpointer = checkpointer


def build_initial_state(
    *,
    user_request: str,
    run_id: str,
    thread_id: str = "",
    mode: GoalMode = GoalMode.EXECUTE,
    plan_tasks: list[dict[str, Any]] | None = None,
    verify_checks: list[dict[str, Any]] | None = None,
    artifact_request: dict[str, Any] | None = None,
) -> AlphaGraphState:
    """Build the initial state a run is invoked with.

    Every field is filled, not left to chance: a partially-initialised state
    would let a node read ``None`` where it expects a list and fail with a
    ``TypeError`` that names no cause.
    """
    return {
        "run_id": run_id,
        "goal_id": "",
        "graph_id": new_graph_id(),
        "thread_id": thread_id,
        "intent": GraphIntent(mode.value),
        "status": GraphStatus.CREATED,
        "current_node": "",
        "next_node": "normalize_request",
        "user_request": user_request,
        "plan_tasks": list(plan_tasks or []),
        "verify_checks": list(verify_checks or []),
        "artifact_request": dict(artifact_request or {}),
        "task_ids": [],
        "ready_task_ids": [],
        "active_task_ids": [],
        "completed_task_ids": [],
        "blocked_task_ids": [],
        "model_calls_used": 0,
        "tool_calls_used": 0,
        "planning_iteration": 0,
        "verify_cycle": 0,
        "repair_attempts": 0,
        "retry_count": 0,
        "cancel_requested": False,
        "approval_request_id": None,
        "failure_summary": None,
        "final_result": None,
        "started_at": "",
        "deadline_at": None,
    }


class GraphRuntime:
    """A compiled graph plus the ledger it writes to.

    The runtime is a thin wrapper: one compiled ``StateGraph``, one
    ``TaskLedger``, and the config that caps concurrency. The Gateway holds one
    runtime per process and reuses it across requests.
    """

    def __init__(self, graph: Any, ledger: TaskLedger, config: GraphRuntimeConfig | None = None) -> None:
        self._graph = graph
        self._ledger = ledger
        self._config = config or GraphRuntimeConfig()

    @property
    def graph(self) -> Any:
        return self._graph

    @property
    def ledger(self) -> TaskLedger:
        return self._ledger

    def invoke(
        self,
        state: AlphaGraphState,
        *,
        config: dict[str, Any] | None = None,
    ) -> AlphaGraphState:
        """Run the graph synchronously and return the final state.

        Synchronous because the ledger is synchronous SQLite; a caller on an
        event loop must run this in a thread (``asyncio.to_thread``).

        The ledger travels in ``configurable``, **not** in the state mapping.
        State is checkpointed; a live SQLite connection is not serialisable and
        would either crash the first checkpoint write or bloat every checkpoint
        with a store object. ``_get_ledger`` reads ``configurable`` first and
        falls back to ``state["_ledger"]`` only for callers that drive nodes
        directly.
        """
        runnable = dict(config or {})
        runnable["configurable"] = {
            **runnable.get("configurable", {}),
            "_ledger": self._ledger,
        }
        runnable_state = {key: value for key, value in state.items() if key != "_ledger"}
        return self._graph.invoke(runnable_state, config=runnable)  # type: ignore[return-value]


def build_graph(*, ledger: TaskLedger | None = None, config: GraphRuntimeConfig | None = None) -> Any:
    """Build and compile the top-level execution graph.

    Called once per process. The returned graph is immutable and shareable
    across requests; the mutable per-run state is supplied to
    :meth:`GraphRuntime.invoke`, not baked into the compiled graph.

    Why conditional edges and not static ones
    -----------------------------------------
    An earlier version declared eleven static edges *and* a ``next_node`` field
    on every node result. LangGraph follows the **static edge**, so the
    ``next_node`` hints were dead: ``dispatch_worker`` could report
    ``final_verify`` (a task parked for approval, a plan with nothing
    dispatchable) and the graph would still step to ``capture_artifact``, then
    back through ``select_ready_tasks`` into ``dispatch_worker`` forever -- a loop
    that only stopped when LangGraph's recursion ceiling turned it into a
    ``GraphRecursionError`` after ~10,000 spins. The topology a node needs is
    genuinely conditional (approval vs. dispatch vs. finish), so it is expressed
    as one conditional edge driven by ``next_node``.

    That also removes the drift risk: with eleven static edges, adding a node
    meant editing two places that had to agree, and nothing checked it. Now the
    node's own ``next_node`` is the only statement of where it goes, and
    :data:`VALID_SUCCESSORS` is the set it may choose from.
    """
    builder = StateGraph(AlphaGraphState)

    for name, node in (
        ("normalize_request", normalize_request),
        ("collect_context", collect_context),
        ("plan_goal", plan_goal),
        ("validate_plan", validate_plan),
        ("select_ready_tasks", select_ready_tasks),
        ("dispatch_worker", dispatch_worker),
        ("capture_artifact", capture_artifact),
        ("verify_task", verify_task),
        ("repair_task", repair_task),
        ("approval_gate", approval_gate),
        ("final_verify", final_verify),
        ("report_result", report_result),
    ):
        builder.add_node(name, node)

    builder.add_edge(START, "normalize_request")

    # Every node routes through the one conditional edge. ``_route_next`` reads
    # ``next_node`` and returns the successor, refusing an unknown one rather
    # than defaulting to END -- a default would silently drop a run whose node
    # named a successor that does not exist.
    builder.add_conditional_edges("normalize_request", _route_next)
    builder.add_conditional_edges("collect_context", _route_next)
    builder.add_conditional_edges("plan_goal", _route_next)
    builder.add_conditional_edges("validate_plan", _route_next)
    builder.add_conditional_edges("select_ready_tasks", _route_next)
    builder.add_conditional_edges("dispatch_worker", _route_next)
    builder.add_conditional_edges("capture_artifact", _route_next)
    builder.add_conditional_edges("verify_task", _route_next)
    builder.add_conditional_edges("repair_task", _route_next)
    builder.add_conditional_edges("approval_gate", _route_next)
    builder.add_conditional_edges("final_verify", _route_next)
    builder.add_conditional_edges("report_result", _route_next)

    # The checkpointer is bound by the Gateway when it has one. Compiling
    # without one is valid: the ledger is the authoritative store, so a run
    # without a checkpointer is still durable, only not resumable mid-graph.
    checkpointer = None
    if config is not None and config.enable_checkpoints:
        checkpointer = getattr(config, "checkpointer", None)

    return builder.compile(checkpointer=checkpointer)


def build_graph_runtime(
    *,
    ledger: TaskLedger | None = None,
    config: GraphRuntimeConfig | None = None,
) -> GraphRuntime:
    """Build a :class:`GraphRuntime` with a compiled graph and a ledger."""
    resolved_ledger = ledger if ledger is not None else TaskLedger()
    graph = build_graph(ledger=resolved_ledger, config=config)
    return GraphRuntime(graph, resolved_ledger, config)
