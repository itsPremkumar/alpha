"""Built-in tool for the legacy continuous-goal surface (RETIRED for mutation).

`RunManager` is the sole run-lifecycle owner, and nothing in production ever
dispatched a goal created through this tool — so `start` / `step` / `pause` /
`resume` refuse by name instead of writing rows that read like launched work.
`list` / `inspect` / `retire` remain: a read that says "these records prove
nothing" is the useful half of the retirement, and `retire` lets an operator
stamp that onto the stored rows without deleting any of them.

Both the refusal text and the read projection come from
`alpha.harness.continuous`, so this tool and the `/loop:*` commands cannot drift
into telling the model two different stories about the same record.
"""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.harness.continuous.models import LEGACY_MIGRATION_GUIDANCE, LEGACY_RETIREMENT_REASON
from alpha.harness.continuous.store import describe_goal, get_goal_store

_LIST_LIMIT = 50


def _retired(action: str, goal_id: str = "") -> str:
    target = f" for goal '{goal_id}'" if goal_id else ""
    return f"goal_engine action='{action}' is retired{target}. {LEGACY_RETIREMENT_REASON} Nothing was created, run, or recorded as progress. {LEGACY_MIGRATION_GUIDANCE}"


@tool("goal_engine", parse_docstring=True)
def goal_engine_tool(
    action: Literal["start", "inspect", "step", "pause", "resume", "list", "retire"],
    goal_id: str = "",
    title: str = "",
    description: str = "",
    max_iterations: int = 100,
) -> str:
    """Inspect the retired continuous-goal record store.

    This tool no longer launches work. `RunManager` owns every real run: send the
    objective as a normal message in a thread instead. The actions below are the
    honest remainder — `start`, `step`, `pause` and `resume` refuse by name, and
    `list` / `inspect` read stored records while stating plainly that none of
    them is evidence that a step ran or that work was verified.

    Args:
        action: 'list', 'inspect' or 'retire' are answered; 'start', 'step', 'pause' and 'resume' are refused as retired.
        goal_id: Identifier of a goal (required for inspect).
        title: Unused; 'start' is retired and records nothing.
        description: Unused; 'start' is retired and records nothing.
        max_iterations: Unused; 'start' is retired and records nothing.
    """
    store = get_goal_store()

    # A corrupt store used to answer "No active autonomous goals." here, which
    # told the model there was no work when the truth was that its work could
    # not be read. Say so instead, and name the file so the operator can act.
    if store.is_degraded:
        return f"Error: the goal store at {store.storage_path} could not be read ({store.load_error}). Goal state is UNKNOWN — this is not an empty catalog. Repair or remove the file, then retry."

    if action == "list":
        goals = store.list_goals()
        if not goals:
            return "No autonomous goal records are stored. (This store is retired: a record here is never evidence that work ran.)"
        counts: dict[str, int] = {}
        for goal in goals:
            counts[goal.status] = counts.get(goal.status, 0) + 1
        out = [
            f"=== Retired Goal Records ({len(goals)} total) ===",
            "Recorded status counts: " + ", ".join(f"{status}={count}" for status, count in sorted(counts.items())),
            f"Every record below is RETIRED and unverified: {LEGACY_RETIREMENT_REASON}",
            LEGACY_MIGRATION_GUIDANCE,
        ]
        for goal in goals[:_LIST_LIMIT]:
            view = describe_goal(goal)
            out.append(f"- **{view['goal_id']}**: {view['title']} [Retired; recorded status `{view['recorded_status']}`, iteration {view['iteration']}/{view['max_iterations']}, verified: never]")
        if len(goals) > _LIST_LIMIT:
            out.append(f"- {len(goals) - _LIST_LIMIT} additional record(s) omitted; inspect a goal by ID for details.")
        return "\n".join(out)

    if action == "inspect":
        if not goal_id:
            return "Error: 'goal_id' is required for 'inspect'."
        goal = store.get_goal(goal_id)
        if goal is None:
            return f"Error: Goal '{goal_id}' not found."
        view = describe_goal(goal)
        out = [
            f"=== Retired Goal Record: {goal.goal_id} ===",
            f"Title: {goal.title}",
            f"Recorded status: {view['recorded_status']} | Verified: never | Iteration {view['iteration']}/{view['max_iterations']}",
            f"Last heartbeat: {view['heartbeat_at']} (a stale timestamp is not liveness; nothing polls it)",
            f"Retired because: {view['retirement_reason']}",
            "\n--- Milestones (none executed) ---",
        ]
        for milestone in view["milestones"]:
            deps = f" (Depends on: {milestone['dependencies']})" if milestone["dependencies"] else ""
            out.append(f"- [UNVERIFIED] {milestone['milestone_id']}: {milestone['title']}{deps} (Attempts: {milestone['attempts']}; recorded `{milestone['recorded_status']}`)")
        if view["strategy_notes"]:
            out.append("\n--- Strategy Notes (historical) ---")
            out.extend(view["strategy_notes"])
        out.append(f"\n{LEGACY_MIGRATION_GUIDANCE}")
        return "\n".join(out)

    if action == "retire":
        outcome = store.retire_all()
        if not outcome["durable"]:
            return f"Error: the retirement stamp did NOT persist to {store.storage_path} ({outcome['error']}). Records are unchanged on disk."
        return f"Stamped {outcome['retired']} of {outcome['total']} record(s) retired in {store.storage_path}. No record was deleted. {LEGACY_RETIREMENT_REASON}"

    # start / step / pause / resume: refused by name, never silently accepted.
    return _retired(action, goal_id)
