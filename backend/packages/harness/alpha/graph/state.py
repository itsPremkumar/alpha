"""The LangGraph state schema for the graph plane's top-level execution graph.

Design principles
-----------------
**State is a projection, not the store.** The ledger
(:class:`~alpha.graph.ledger.TaskLedger`) is the authoritative store for goals,
tasks, attempts, artifacts, claims, events, and approvals. This state holds
**only what the graph needs to route and bound**: IDs, counters, flags, and the
current node's local scratch. A task's full model, an artifact's metadata, a
claim's provenance -- those live in the ledger and are fetched by ID when a
node needs them.

This is the plan's §6.3 rule: "Keep state minimal and avoid putting huge files,
screenshots, or long tool transcripts into the checkpoint. Store large payloads
in artifact storage and keep references in state."

**Reducers are explicit and deterministic.** Every list field uses
``Annotated[list[T], _append_dedupe]`` so two parallel branches appending the same
ID produce one entry, not two. A ``last_writer_wins`` merge on a list is a race
that produces non-deterministic state, which makes replay and debugging
impossible.

**Terminal states are typed.** ``GraphStatus`` is separate from
``GoalStatus`` and ``TaskStatus`` because the graph's lifecycle is different
from a goal's: a graph can be ``waiting_approval`` while its goal is
``running``, and conflating them would make the approval gate indistinguishable
from a normal pause.

The schema
----------
``AlphaGraphState`` is a ``TypedDict`` with ``total=False`` so a node can return
a partial update (``{"status": "running"}``) without having to know every field.
LangGraph merges the returned dict into the current state using the reducers,
so a partial update is safe.

Fields are grouped by concern:

* **Identity**: ``run_id``, ``goal_id``, ``graph_id``, ``thread_id``
* **Routing**: ``intent``, ``status``, ``current_node``, ``next_node``
* **Task tracking**: ``task_ids``, ``ready_task_ids``, ``active_task_ids``,
  ``completed_task_ids``, ``blocked_task_ids``
* **Counters**: ``model_calls_used``, ``tool_calls_used``, ``planning_iteration``,
  ``verify_cycle``, ``repair_attempts``, ``retry_count``
* **Flags**: ``cancel_requested``, ``approval_request_id``
* **Results**: ``failure_summary``, ``final_result``
* **Timing**: ``started_at``, ``deadline_at``

What is deliberately **not** in state
--------------------------------------
* **Task models**: fetched from the ledger by ID.
* **Artifact payloads**: referenced by ID, stored on disk.
* **Claim provenance**: lives in the claims table.
* **Event history**: appended to the event store, not accumulated in state.
* **Model/tool outputs**: returned as node results, not stored in state. A
  node that needs to pass a large output to the next node writes it as an
  artifact and puts the artifact ID in state.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict

__all__ = [
    "AlphaGraphState",
    "GraphStatus",
    "GraphIntent",
    "append_dedupe",
    "merge_graph_state",
]


class GraphStatus(StrEnum):
    """The graph's own lifecycle status.

    Separate from ``GoalStatus`` and ``TaskStatus`` because the graph's
    lifecycle is different: a graph can be ``waiting_approval`` while its goal
    is ``running``, and conflating them would make the approval gate
    indistinguishable from a normal pause.

    ``RUNNING`` is the coarse aggregate a caller outside the graph can use
    without enumerating the eight fine-grained in-flight statuses. Inside the
    graph, a node reports the specific status so the UI can say *which* phase
    is active rather than only "running".
    """

    CREATED = "created"
    ROUTING = "routing"
    PLANNING = "planning"
    VALIDATING = "validating"
    SCHEDULING = "scheduling"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    WAITING_APPROVAL = "waiting_approval"
    FINAL_VERIFYING = "final_verifying"
    REPORTING = "reporting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GraphIntent(StrEnum):
    """The three intents the graph distinguishes (plan §4.3)."""

    ANSWER = "answer"
    PLAN = "plan"
    EXECUTE = "execute"


def append_dedupe(left: list[str] | None, right: list[str] | None) -> list[str]:
    """Reducer: append ``right`` to ``left``, deduplicating.

    Used for every ID list in state. Two parallel branches appending the same
    ID produce one entry, not two, which is what makes parallel execution
    deterministic. The order is preserved (left first, then right) so a
    consumer can rely on insertion order for display.
    """
    merged: list[str] = list(left or [])
    for item in right or []:
        if item not in merged:
            merged.append(item)
    return merged


def _counter(left: int | None, right: int | None) -> int:
    """Reducer: take the maximum of two counters.

    A counter that can only go up is the right shape for "how many times have
    we done this" -- it is monotonic, so a retry cannot accidentally reset it,
    and two parallel branches cannot both increment and lose one.
    """
    return max(left or 0, right or 0)


class AlphaGraphState(TypedDict, total=False):
    """The state schema for the graph plane's top-level execution graph.

    ``total=False`` so a node can return a partial update. LangGraph merges the
    returned dict into the current state using the reducers.
    """

    # Identity
    run_id: str
    goal_id: str
    graph_id: str
    thread_id: str

    # Routing
    intent: GraphIntent
    status: GraphStatus
    current_node: str
    next_node: str

    # Caller-provided input (bounded, not a store)
    user_request: str
    plan_tasks: list[dict[str, Any]]
    verify_checks: list[dict[str, Any]]
    artifact_request: dict[str, Any]

    # Injected dependency (never checkpointed; the ledger is the durable store)
    _ledger: Any

    # Task tracking (IDs only; models live in the ledger)
    task_ids: Annotated[list[str], append_dedupe]
    ready_task_ids: Annotated[list[str], append_dedupe]
    active_task_ids: Annotated[list[str], append_dedupe]
    completed_task_ids: Annotated[list[str], append_dedupe]
    blocked_task_ids: Annotated[list[str], append_dedupe]

    # Counters (monotonic)
    model_calls_used: Annotated[int, _counter]
    tool_calls_used: Annotated[int, _counter]
    planning_iteration: Annotated[int, _counter]
    verify_cycle: Annotated[int, _counter]
    repair_attempts: Annotated[int, _counter]
    retry_count: Annotated[int, _counter]

    # Flags
    cancel_requested: bool
    approval_request_id: str | None

    # Results
    failure_summary: str | None
    final_result: str | None

    # Timing
    started_at: str
    deadline_at: str | None


def merge_graph_state(current: AlphaGraphState, update: dict[str, Any]) -> AlphaGraphState:
    """Merge a partial update into the current state, applying reducers.

    This is the function LangGraph calls when a node returns a dict. It is
    exposed separately so tests can pin the merge behavior without running a
    graph, and so a caller outside LangGraph (e.g. a Gateway route) can apply
    the same merge semantics.

    Fields not in the update are carried through unchanged. Fields in the
    update that are not in the schema are **refused** rather than ignored,
    because a typo in a field name should be a loud error, not a silent no-op.
    """
    merged: dict[str, Any] = dict(current)
    for key, value in update.items():
        if key not in AlphaGraphState.__annotations__:
            raise ValueError(f"unknown graph state field: {key!r}")
        if key in ("task_ids", "ready_task_ids", "active_task_ids", "completed_task_ids", "blocked_task_ids"):
            merged[key] = append_dedupe(current.get(key), value)
        elif key in ("model_calls_used", "tool_calls_used", "planning_iteration", "verify_cycle", "repair_attempts", "retry_count"):
            merged[key] = _counter(current.get(key), value)
        else:
            merged[key] = value
    return merged  # type: ignore[return-value]
