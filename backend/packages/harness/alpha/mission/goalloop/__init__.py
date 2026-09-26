"""Standing goals: a loop that keeps working until the work is actually done.

This is alpha's answer to the one thing Hermes' goal loop does better: a single
session, one objective, an independent judge after every turn, and a
deterministic command that has to exit 0 before "done" is even on the table.

The design reuses alpha's own depth rather than replacing it. ``mission/``
already owns the acceptance discipline - a criterion is UNVERIFIED until
something measured it - and the live agent chain already runs an in-turn
evidence verifier. What was missing was the *cross-turn* loop: a standing
objective, an auxiliary judge that is independent of the agent's own claim of
success, and a deterministic gate. Those live here, and the boundaries of the
module are drawn so none of this needs to weaken an existing guard.

Layering, outermost first:

* :mod:`verdict` - the closed verdict grammar. Unparseable is not ``done``.
* :mod:`contract` - five optional fields naming what done means.
* :mod:`gates` - deterministic commands, run first, bounded, never cached.
* :mod:`state` - the goal, its budget, its subgoals, its gates, on disk.
* :mod:`judge` - the auxiliary checker, conservative and fail-open.
* :mod:`engine` - the boundary: gates, then judge, then a plain user message.
* :mod:`bindings` - the ``/goal``, ``/subgoal`` and ``/goal gate`` commands.
"""

from __future__ import annotations

from alpha.mission.goalloop.contract import (
    CONTRACT_FIELDS,
    CompletionContract,
    ParsedGoal,
    build_draft_prompt,
    draft_contract,
    parse_goal_text,
)
from alpha.mission.goalloop.engine import (
    BUDGET_EXHAUSTED_MESSAGE,
    CONTINUATION_ROLE,
    GATE_EXHAUSTED_MESSAGE,
    GoalLoopEngine,
    TurnAction,
    TurnDecision,
    build_continuation_prompt,
)
from alpha.mission.goalloop.gates import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TAIL_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    GateReport,
    GateResult,
    GateRunner,
    GateStatus,
    QualityGate,
)
from alpha.mission.goalloop.judge import (
    JUDGE_SYSTEM_PROMPT,
    GoalJudge,
    JudgeOutcome,
    JudgeRequest,
    build_request,
    make_model_judge,
)
from alpha.mission.goalloop.state import (
    DEFAULT_MAX_TURNS,
    GOAL_SCHEMA_ID,
    GoalState,
    GoalStatus,
    GoalStore,
    get_goal_store,
    goals_dir,
    new_goal_id,
    reset_goal_store,
)
from alpha.mission.goalloop.verdict import (
    JUDGE_UNAVAILABLE_REASON,
    JUDGE_UNREADABLE_REASON,
    GoalVerdict,
    ParsedVerdict,
    parse_verdict,
)

__all__ = [
    "BUDGET_EXHAUSTED_MESSAGE",
    "CONTRACT_FIELDS",
    "CONTINUATION_ROLE",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TAIL_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "GATE_EXHAUSTED_MESSAGE",
    "GOAL_SCHEMA_ID",
    "JUDGE_SYSTEM_PROMPT",
    "JUDGE_UNAVAILABLE_REASON",
    "JUDGE_UNREADABLE_REASON",
    "CompletionContract",
    "GateReport",
    "GateResult",
    "GateRunner",
    "GateStatus",
    "GoalJudge",
    "GoalLoopEngine",
    "GoalState",
    "GoalStatus",
    "GoalStore",
    "GoalVerdict",
    "JudgeOutcome",
    "JudgeRequest",
    "ParsedGoal",
    "ParsedVerdict",
    "QualityGate",
    "TurnAction",
    "TurnDecision",
    "build_continuation_prompt",
    "build_draft_prompt",
    "build_request",
    "draft_contract",
    "get_goal_store",
    "goals_dir",
    "make_model_judge",
    "new_goal_id",
    "parse_goal_text",
    "parse_verdict",
    "reset_goal_store",
]
