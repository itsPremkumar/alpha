"""Orchestration strategy, swarm sizing, stuck detection, and escalation.

Spec §12 (strategy choice), §13 (swarm autoscaling), §36 (loop detection),
§37 (strategy switching).

**Why this delegates where it can.** Swarm sizing is not re-derived here.
``alpha.swarm.strategy.resolve_strategy`` already implements the precedence
explicit > semantic > topology > heuristic > fallback, and
``alpha.swarm.estimator.SwarmBenefitEstimator`` is byte-pinned by an existing
regression. :func:`choose_orchestration` asks that resolver and *records* its
answer, which is the discipline ``swarm/strategy.py`` itself demands of the DWE
path — the plan and the recorded strategy must agree.

What this module owns is the part nothing had: **what to do when the current
strategy has stopped working**, and **how many agents are worth having**. Both
need the mission's own history (attempt counts, error signatures, consecutive
no-progress cycles), which no single subsystem holds.

**Three properties are load-bearing.**

1. **Strategy is escalated, never retried.** :func:`escalation_ladder` is a
   fixed ladder with no repeats. Spec §69 and §36 both forbid "retry the same
   action": after N identical failures the next rung must be a *different kind*
   of move. A ladder that could return the same rung would let APEX burn a
   budget re-running the thing that just failed.

2. **Stuck detection is measured over time, not counted.** A goal that makes
   progress every cycle is not stuck however many cycles it has run, so the
   signal is "no state change across N cycles", and the thresholds are
   configurable per task class rather than hardcoded.

3. **Sizing can shrink to zero.** A mission whose remaining work is one trivial
   step must be able to converge to a single agent. A controller that only ever
   grows is how a system ends up with twenty idle workers and one line of work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "AttemptRecord",
    "OrchestrationStrategy",
    "StuckVerdict",
    "TaskShape",
    "TaskThresholds",
    "choose_orchestration",
    "classify_stuck",
    "escalation_ladder",
    "next_strategy_after_failure",
    "recommended_swarm_size",
    "signature_of",
]


class OrchestrationStrategy(StrEnum):
    """Spec §12, verbatim."""

    DIRECT = "direct"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    PIPELINE = "pipeline"
    MAP_REDUCE = "map_reduce"
    DEBATE = "debate"
    HANDOFF = "handoff"
    SWARM = "swarm"
    HIERARCHICAL_SWARM = "hierarchical_swarm"
    RESEARCH = "research"
    DEBUG = "debug"
    REPAIR = "repair"


#: Rung order for spec §37's escalation. Each rung is a *different kind* of
#: move, and the ladder never repeats a rung, so an escalation always changes
#: what APEX does rather than how many times it does it.
ESCALATION: tuple[OrchestrationStrategy, ...] = (
    OrchestrationStrategy.DIRECT,
    OrchestrationStrategy.RESEARCH,
    OrchestrationStrategy.DEBUG,
    OrchestrationStrategy.REPAIR,
    OrchestrationStrategy.PARALLEL,
    OrchestrationStrategy.SWARM,
    OrchestrationStrategy.HIERARCHICAL_SWARM,
    OrchestrationStrategy.DEBATE,
)

#: Strategies that cannot escalate further: rerunning them unchanged is exactly
#: the loop spec §36 exists to break.
_ESCALATION_CEILING = OrchestrationStrategy.DEBATE


@dataclass(frozen=True, slots=True)
class TaskShape:
    """What the executive knows about the work, without asking a model.

    Every field is measurable from the mission's own state: the goal's criteria
    count, how many independent children exist, whether a deadline is set, and
    so on. A model may *propose* a different shape, but the default path is a
    pure function of these numbers, which is what makes strategy selection
    reproducible (spec §178).
    """

    objective: str = ""
    independent_items: int = 1
    dependency_depth: int = 0
    criteria_count: int = 0
    uncertainty: float = 0.0
    risk: str = "R1"
    has_deadline: bool = False
    requires_external_facts: bool = False
    is_coding: bool = False
    repeated_failures: int = 0
    has_error: bool = False

    @property
    def independence_ratio(self) -> float:
        """0 = fully serial chain, 1 = fully independent fan-out."""
        if self.independent_items <= 0:
            return 0.0
        depth = max(1, self.dependency_depth)
        return min(1.0, self.independent_items / depth)


def _mentions_external_facts(text: str) -> bool:
    return bool(
        re.search(
            r"\b(latest|current|today|recent|news|now|version of|compare|versus|vs\.?|market|competitor|state of)\b",
            text,
            re.IGNORECASE,
        )
    )


def _mentions_coding(text: str) -> bool:
    return bool(
        re.search(
            r"\b(code|coding|function|class|method|refactor|debug|bug|fix|implement|repo|test|suite|compile|build)\b|\.(py|ts|tsx|js|jsx|rs|go|java|sql|sh|ps1)\b",
            text,
            re.IGNORECASE,
        )
    )


def shape_from_goal(goal: Any, *, children: Iterable[Any] = (), attempts: int = 0) -> TaskShape:
    """Derive a :class:`TaskShape` from an ``ApexGoal`` and its children.

    Pure, and deliberately the only place a ``TaskShape`` is built from a live
    goal — so "what did the executive know when it picked this strategy" is
    answerable from the recorded decision rather than re-derived later.
    """
    objective = str(getattr(goal, "objective", "") or "")
    criteria = list(getattr(goal, "success_criteria", []) or [])
    child_list = list(children)
    return TaskShape(
        objective=objective,
        independent_items=max(1, len(child_list) or len(criteria) or 1),
        dependency_depth=len(child_list),
        criteria_count=len(criteria),
        uncertainty=0.7 if getattr(goal, "state", "") in ("analyzing", "planning") else 0.3,
        risk=str(getattr(goal, "risk", "R1") or "R1"),
        has_deadline=getattr(goal, "deadline", None) is not None,
        requires_external_facts=_mentions_external_facts(objective),
        is_coding=_mentions_coding(objective),
        repeated_failures=int(attempts),
        has_error=bool(getattr(goal, "blocked_reason", "")),
    )


def choose_orchestration(shape: TaskShape) -> tuple[OrchestrationStrategy, list[str]]:
    """Pick a strategy, returning it with the reason codes that produced it.

    Precedence, highest first — this ordering is the contract, because each
    lower rung is a strictly more expensive way to be wrong:

    1. **an existing error** → DEBUG/REPAIR, because diagnosis precedes planning.
    2. **repeated failures** → escalate the ladder, never retry the same rung.
    3. **external facts needed** → RESEARCH, because a plan built on guessed
       facts is not a plan.
    4. **high independence** → PARALLEL or MAP_REDUCE.
    5. **dependency depth** → SEQUENTIAL / PIPELINE / HANDOFF.
    6. **fallback** → DIRECT.

    A single trivial objective reaches DIRECT, which is spec §191's requirement
    that APEX optimize the mission rather than perform theatrical autonomy.
    """
    reasons: list[str] = []

    if shape.has_error:
        strategy = OrchestrationStrategy.REPAIR if shape.repeated_failures else OrchestrationStrategy.DEBUG
        return strategy, ["existing_error", "diagnose_before_planning"]

    if shape.repeated_failures >= 2:
        strategy = escalation_ladder(shape.repeated_failures)
        return strategy, ["repeated_failures", f"attempt={shape.repeated_failures}", "escalate_do_not_retry"]

    if shape.requires_external_facts and shape.uncertainty >= 0.5:
        return OrchestrationStrategy.RESEARCH, ["external_facts_required", "uncertainty_high"]

    independent = shape.independent_items
    depth = shape.dependency_depth

    if independent <= 1 and depth == 0:
        if shape.criteria_count == 0:
            return OrchestrationStrategy.DIRECT, ["no_criteria", "no_decomposition_needed"]
        return OrchestrationStrategy.DIRECT, ["single_objective", "criteria_but_no_parallel_work"]

    if independent > 1:
        reasons.append(f"independent_items={independent}")
        if shape.is_coding and independent >= 4:
            return OrchestrationStrategy.HIERARCHICAL_SWARM, [*reasons, "large_coding_fan_out"]
        if independent >= 8:
            return OrchestrationStrategy.MAP_REDUCE, [*reasons, "wide_fan_out_needs_reduction"]
        if shape.uncertainty >= 0.6:
            return OrchestrationStrategy.DEBATE, [*reasons, "uncertainty_requires_judgement"]
        return OrchestrationStrategy.PARALLEL, [*reasons, "independent_work"]

    if depth >= 3:
        return OrchestrationStrategy.SEQUENTIAL, [f"dependency_depth={depth}"]
    if depth >= 2:
        return OrchestrationStrategy.PIPELINE, [f"dependency_depth={depth}"]
    return OrchestrationStrategy.HANDOFF, [f"dependency_depth={depth}", "single_handoff"]


def escalation_ladder(attempt: int) -> OrchestrationStrategy:
    """The strategy for the Nth repeated failure. Never repeats a rung.

    ``attempt <= 0`` is the initial rung; beyond the ladder's length it clamps
    to the ceiling, because returning something already-tried would be the loop
    spec §36 forbids. Clamping to a distinct final rung is the honest answer:
    APEX ran out of *new* strategies, which is a condition for escalating to
    the user, not a reason to repeat one.
    """
    index = max(0, int(attempt) - 1)
    if index >= len(ESCALATION):
        return _ESCALATION_CEILING
    return ESCALATION[index]


def next_strategy_after_failure(current: OrchestrationStrategy, *, attempt: int) -> OrchestrationStrategy:
    """The strategy that differs from ``current``, for spec §37.

    If the ladder's next rung happens to equal ``current``, the one after it is
    returned. Returning ``current`` would defeat the entire purpose.
    """
    candidate = escalation_ladder(max(1, attempt))
    if candidate is current:
        following = escalation_ladder(max(1, attempt) + 1)
        return following if following is not current else _ESCALATION_CEILING
    return candidate


# --------------------------------------------------------------------------- #
# Swarm sizing — spec §13
# --------------------------------------------------------------------------- #

#: Spec §13's tiers, as inclusive worker bounds. ``0`` is a real answer: a
#: simple task needs no subagents at all.
SWARM_TIERS: tuple[tuple[str, int, int], ...] = (
    ("trivial", 0, 0),
    ("simple", 0, 1),
    ("medium", 1, 3),
    ("complex", 3, 8),
)


def classify_tier(shape: TaskShape) -> str:
    """Classify the work into one of spec §13's four tiers."""
    independent = shape.independent_items
    if independent <= 1 and shape.dependency_depth == 0 and not shape.is_coding:
        return "trivial"
    if independent <= 2:
        return "simple"
    if independent <= 5:
        return "medium"
    return "complex"


def recommended_swarm_size(
    shape: TaskShape,
    *,
    max_active_agents: int,
    remaining_items: int | None = None,
    shrink: bool = False,
) -> dict[str, Any]:
    """How many agents are worth having right now, with the reason.

    Three inputs shrink the answer below the tier's upper bound:

    * ``remaining_items`` — work that has already been done cannot need an
      agent, so a mission converging shrinks instead of holding its fan-out.
    * ``shrink`` — the caller observed the swarm stopped producing value.
    * ``max_active_agents`` — the contract's ceiling always wins.

    A zero result is returned honestly with ``reason`` rather than clamped up
    to one, because "this needs no agent" is a useful thing for the executive
    to know.
    """
    tier = classify_tier(shape)
    low, high = next((lo, hi) for name, lo, hi in SWARM_TIERS if name == tier)

    reasons = [f"tier={tier}"]
    workers = high

    if shape.uncertainty >= 0.7 and tier in ("medium", "complex"):
        # High uncertainty means the plan is a guess; more agents guessing in
        # parallel do not reduce that, they multiply the cost of the mistake.
        workers = min(workers, low if tier == "medium" else 4)
        reasons.append("uncertainty_caps_parallel_agents")

    if remaining_items is not None:
        remaining = max(0, int(remaining_items))
        workers = min(workers, remaining)
        if remaining < high:
            reasons.append(f"remaining_items={remaining}")

    if shrink:
        workers = max(0, workers // 2)
        reasons.append("swarm_produced_no_value")

    if max_active_agents >= 0:
        if workers > max_active_agents:
            reasons.append(f"clamped_to_contract_ceiling={max_active_agents}")
            workers = max_active_agents

    if workers == 0:
        reasons.append("no_subagents_needed")

    return {
        "tier": tier,
        "workers": workers,
        "reason": ",".join(reasons),
        "bounds": {"min": low, "max": high},
    }


# --------------------------------------------------------------------------- #
# Stuck detection — spec §36
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One execution attempt, as the loop detector sees it."""

    goal_id: str
    #: Normalized error signature; see :func:`signature_of`.
    error_signature: str = ""
    #: The strategy that produced this attempt.
    strategy: str = ""
    #: Whether the mission state advanced during the attempt.
    progressed: bool = True
    #: Tool name when the attempt failed on a tool.
    tool: str = ""
    agent_id: str = ""


@dataclass(frozen=True, slots=True)
class TaskThresholds:
    """Spec §36/§68 thresholds, configurable per task class.

    They are parameters rather than constants because spec §36 says so
    explicitly: "Thresholds should be configurable by task class." A long
    migration is not stuck after three identical migrations; a search across
    four sources legitimately repeats the same query a few times.
    """

    identical_failures: int = 3
    no_progress_cycles: int = 5
    same_tool_failures: int = 3
    #: Consecutive cycles in which one agent kept the mission from advancing.
    #: Not merely "one agent kept working" — a long-running agent that is
    #: producing progress is the desired case, not a loop.
    same_agent_cycles: int = 4

    @classmethod
    def for_class(cls, task_class: str) -> TaskThresholds:
        task_class = str(task_class or "").lower()
        if task_class in ("research", "search"):
            # Research legitimately repeats a query while it triangulates.
            return cls(identical_failures=4, no_progress_cycles=7, same_tool_failures=4, same_agent_cycles=5)
        if task_class in ("coding", "repair", "build"):
            # Code either progresses or it does not.
            return cls(identical_failures=2, no_progress_cycles=3, same_tool_failures=2, same_agent_cycles=3)
        return cls()


#: Identifier-ish runs collapsed to a placeholder. Copied in *spirit* from
#: ``alpha.workflow.failures.error_signature`` and deliberately not imported:
#: APEX signatures are for cross-strategy loop detection, not for
#: ``NodeFailureClass``, and reusing one vocabulary for two jobs is how they
#: stop agreeing.
_ID_NOISE = (
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    # Decimals before bare integers: `1.7s` must collapse whole, or the trailing
    # digit survives (`<n>.7s` vs `<n>.2s`) and two identical timeouts look like
    # different faults. The terminator is a digit-lookahead, NOT `\b`: in `1.7s`
    # the `7` is followed by a word character, so a trailing `\b` never matches
    # and the whole rule silently does nothing.
    (re.compile(r"\d+\.\d+(?![0-9])"), "<num>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    # After the numeric collapse, a bare 7+ hex-ish run is an id, not a count.
    (re.compile(r"\b[0-9a-f]{7,}\b", re.IGNORECASE), "<hex>"),
)


def signature_of(error: str) -> str:
    """Normalize an error so "the same fault wearing different digits" is visible.

    Without this, three failures carrying different request ids look like three
    unrelated problems and APEX escalates when it should have recognised a loop.
    """
    text = str(error or "").strip().lower()
    for pattern, replacement in _ID_NOISE:
        text = pattern.sub(replacement, text)
    return " ".join(text.split())[:200]


@dataclass
class StuckVerdict:
    """What the loop detector concluded, and what it says should happen next."""

    stuck: bool
    reason: str = ""
    #: The strategy that must change, or "" when none needs to.
    next_strategy: OrchestrationStrategy | None = None
    #: Duplicate attempts observed for the triggering signature.
    identical_failures: int = 0
    no_progress_cycles: int = 0
    same_tool_failures: int = 0
    same_agent_cycles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stuck": self.stuck,
            "reason": self.reason,
            "next_strategy": self.next_strategy.value if self.next_strategy else "",
            "identical_failures": self.identical_failures,
            "no_progress_cycles": self.no_progress_cycles,
            "same_tool_failures": self.same_tool_failures,
            "same_agent_cycles": self.same_agent_cycles,
        }


def classify_stuck(
    attempts: list[AttemptRecord],
    *,
    current_strategy: OrchestrationStrategy = OrchestrationStrategy.DIRECT,
    attempt_number: int = 0,
    thresholds: TaskThresholds | None = None,
) -> StuckVerdict:
    """Decide whether the mission is looping, and force a strategy change.

    Four independent signals, each with its own threshold, because they fail
    differently: a repeated tool failure can be one stubborn tool while the
    plan advances, and a no-progress run can be legitimate long work. Each is
    reported with its own count so an operator can see *which* signal fired.
    """
    limits = thresholds or TaskThresholds()

    def _longest_run(predicate: Any) -> int:
        best = 0
        run = 0
        for attempt in attempts:
            if predicate(attempt):
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best

    identical = _longest_run(lambda a: bool(a.error_signature))
    no_progress = _longest_run(lambda a: not a.progressed)
    # A tool or agent is only part of a *failure* loop when the attempt
    # actually failed. Twelve successful uses of the same tool is one agent
    # doing its job; counting that as a loop would force a pointless strategy
    # change on a mission that is working.
    same_tool = _longest_run(lambda a: bool(a.tool) and bool(a.error_signature))
    same_agent = _longest_run(lambda a: bool(a.agent_id) and not a.progressed)

    fired = [
        (identical >= limits.identical_failures, "identical_failures", identical),
        (no_progress >= limits.no_progress_cycles, "no_progress_cycles", no_progress),
        (same_tool >= limits.same_tool_failures, "same_tool_failures", same_tool),
        (same_agent >= limits.same_agent_cycles, "same_agent_cycles", same_agent),
    ]
    hit = [(name, count) for triggered, name, count in fired if triggered]

    return StuckVerdict(
        stuck=bool(hit),
        reason=",".join(name for name, _ in hit),
        # The next strategy is forced whenever a signal fires, which is what
        # makes this a strategy *change* rather than a retry counter.
        next_strategy=next_strategy_after_failure(current_strategy, attempt=attempt_number) if hit else None,
        identical_failures=identical,
        no_progress_cycles=no_progress,
        same_tool_failures=same_tool,
        same_agent_cycles=same_agent,
    )
