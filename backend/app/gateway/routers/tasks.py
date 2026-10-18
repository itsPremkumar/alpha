"""One run-scoped task projection over every engine (Bot Mode 3.5).

`GET /api/tasks?run_id=` is the single read the graph plane and Team Ops both
consume. It answers "what tasks exist for this run, in the neutral contract,
and from which engine" — the :mod:`alpha.tasks` adapters are the only
translation, so a status the UI renders for a swarm task and a graph task
means the same thing by construction rather than by coincidence.

Honesty rules, enforced by this route:

* **A failed engine read never reads as zero.** Each engine is collected
  independently and reports either its tasks or the reason it could not be
  read (``engine_errors``) — never a silent empty list, which would render
  "this run has no tasks" over a Gateway that merely could not answer.
* **Only engines actually queried appear** in ``engines`` and in the error
  map. Workflow (DWE) runs live in a separate id space and are not collected
  here; saying so is honest, returning phantom zeros for them is not.
* **This route reads and never writes.** It owns no lifecycle, no status and
  no verdict — each engine remains the source of truth for its own tasks.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _graph_contracts(run_id: str) -> list[dict[str, Any]]:
    """Project every graph-plane task of one run through the neutral adapter.

    The ledger is the default instance the graph runtime itself uses when no
    ledger is injected, so this route reads the same store a run wrote to.
    Goal-level acceptance criteria travel with each task: the adapter reads
    them from the caller when the node declares none.
    """
    from alpha.graph.ledger import TaskLedger
    from alpha.tasks.adapters import graph_task_to_contract

    ledger = TaskLedger()
    projected: list[dict[str, Any]] = []
    for goal in ledger.list_goals(run_id=run_id):
        for node in ledger.list_tasks(goal_id=goal.goal_id):
            contract = graph_task_to_contract(
                node,
                run_id=run_id,
                acceptance_criteria=list(goal.acceptance_criteria),
            )
            projected.append(contract.model_dump(mode="json"))
    return projected


def _swarm_contracts(run_id: str) -> list[dict[str, Any]]:
    """Project every swarm task of one run through the neutral adapter."""
    from alpha.swarm.coordinator import get_swarm_coordinator
    from alpha.tasks.adapters import swarm_task_to_contract

    coordinator = get_swarm_coordinator()
    projected: list[dict[str, Any]] = []
    for plan in coordinator.plans_for_run(run_id):
        for task in plan.tasks.values():
            projected.append(swarm_task_to_contract(task, run_id=run_id).model_dump(mode="json"))
    return projected


@router.get("", summary="Every engine's tasks for one run, in the neutral contract")
async def list_run_tasks(run_id: str = Query(min_length=1, max_length=256)) -> dict[str, Any]:
    """The unified, run-scoped task projection (Bot Mode 3.5).

    One query parameter, one vocabulary: graph-plane goals/tasks and swarm
    plans/tasks arrive as `alpha.tasks` `TaskContract` rows carrying their
    `engine` and `native_status`, so nothing is re-mapped client-side. The
    response reports per-engine counts and per-engine read failures, because
    "the graph engine contributed nothing" and "the graph engine could not be
    read" are different facts and only one of them is a task list.
    """
    key = run_id.strip()
    if not key:
        raise HTTPException(status_code=422, detail="run_id is required.")

    contracts: list[dict[str, Any]] = []
    engines: dict[str, int] = {}
    errors: dict[str, str] = {}

    collectors = (
        ("graph", _graph_contracts),
        ("swarm", _swarm_contracts),
    )
    for engine, collect in collectors:
        try:
            rows = await asyncio.to_thread(collect, key)
        except Exception as exc:  # noqa: BLE001 - reported verbatim, never as an empty list
            errors[engine] = f"{type(exc).__name__}: {exc}"
        else:
            engines[engine] = len(rows)
            contracts.extend(rows)

    return {
        "run_id": key,
        "count": len(contracts),
        "engines": engines,
        "engine_errors": errors,
        "tasks": contracts,
    }
